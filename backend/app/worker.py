"""Durable serial worker. Run with python -m app.worker."""
import asyncio
import time
from .config import Config
from .store import Store, uid, now
from .integrations import IntegrationError, GitHubClient, ModelClient
from .security import proxy_for
from .agent import run_agent


class StopTask(Exception):
    pass


class Worker:
    def __init__(self, config, store=None, github_factory=GitHubClient, model_factory=ModelClient):
        self.config=config
        self.store=store or Store(config)
        self.github_factory=github_factory
        self.model_factory=model_factory
        self.owner=uid()

    def check(self, task, kinds=('model','github','proxy')):
        latest=self.store.get('tasks',task['id'])
        if not latest or latest.get('status')=='cancelled':
            raise StopTask()
        if time.time()>task.get('deadline',time.time()+1):
            raise IntegrationError('任务已超过执行时限',kind='budget')
        current=self.store.settings()
        frozen=task['settings'].get('generations',{})
        if any(current['generations'][k]!=frozen.get(k,0) for k in kinds):
            raise IntegrationError('任务使用的凭证已撤销，请重新创建任务',kind='auth')

    def update_task(self, ident, **fields):
        with self.store.connect() as c:
            task=self.store.get('tasks',ident,c)
            if task and task.get('status')!='cancelled':
                task.update(fields,updated_at=now())
                self.store.put('tasks',ident,task,c)

    def finish_if_resolved(self, task_id):
        with self.store.connect() as c:
            task=self.store.get('tasks',task_id,c)
            drafts=[d for d in self.store.all('drafts',c) if d['task_id']==task_id]
            if task and task.get('status')=='waiting_confirmation' and drafts and all(d['status'] in ('succeeded','discarded') for d in drafts):
                task.update(status='completed',updated_at=now())
                self.store.put('tasks',task_id,task,c)

    def claim(self):
        s=self.store
        with s.connect() as c:
            s.put('runtime','worker',{'heartbeat':time.time()},c)
            for d in s.all('drafts',c):
                if d['status']=='executing' and d.get('lease',0)<time.time():
                    d.update(status='unknown',error='发布进程中断，需要核实远端结果',next_check=time.time()+30)
                    s.put('drafts',d['id'],d,c)
                if d['status']=='pending':
                    d.update(status='executing',lease=time.time()+180,owner=self.owner)
                    s.put('drafts',d['id'],d,c)
                    return 'publish',d
                if d['status']=='unknown' and d.get('next_check',0)<=time.time() and d.get('checks',0)<3 and d.get('credential'):
                    d.update(next_check=time.time()+120,checks=d.get('checks',0)+1)
                    s.put('drafts',d['id'],d,c)
                    return 'reconcile',d
            for task in reversed(s.all('tasks',c)):
                if task['kind']=='subscription':
                    if task.get('status') not in ('completed','failed','cancelled'):
                        task.update(status='cancelled',error='订阅刷新已改为设置操作，请在设置页重新刷新',updated_at=now())
                        s.put('tasks',task['id'],task,c)
                    continue
                state=task.get('status')
                eligible=state=='queued' or (state=='running' and task.get('lease',0)<time.time()) or (state=='waiting_rate_limit' and task.get('retry_at',0)<=time.time())
                if eligible:
                    task.update(status='running',lease=time.time()+180,owner=self.owner,updated_at=now())
                    s.put('tasks',task['id'],task,c)
                    return 'task',task
        return None

    async def heartbeat(self, ident, bucket):
        while True:
            await asyncio.sleep(5)
            with self.store.connect() as c:
                self.store.put('runtime','worker',{'heartbeat':time.time()},c)
                item=self.store.get(bucket,ident,c)
                if item and item.get('owner')==self.owner:
                    item['lease']=time.time()+180
                    self.store.put(bucket,ident,item,c)

    async def tick(self):
        cleanup=self.store.get('runtime','proxy_cleanup') or {}
        if cleanup.get('pending'):
            self.store.put('runtime','worker',{'heartbeat':time.time()})
            if cleanup.get('retry_at',0)<=time.time():
                await self.cleanup_proxy()
        for t in self.store.all('tasks'):
            self.finish_if_resolved(t['id'])
        claimed=self.claim()
        if not claimed:
            return False
        kind,item=claimed
        pulse=asyncio.create_task(self.heartbeat(item['id'],'tasks' if kind=='task' else 'drafts'))
        try:
            if kind=='task':
                await self.execute(item)
            else:
                await self.publish(item,reconcile=kind=='reconcile')
        finally:
            pulse.cancel()
            try: await pulse
            except asyncio.CancelledError: pass
        return True

    async def cleanup_proxy(self):
        from .subscriptions import deactivate_mihomo
        def commit():
            with self.store.connect() as c:
                cfg=self.store.settings(c)
                cfg['subscription']['status']='已删除，代理客户端已清理'
                self.store.save_settings(cfg,c)
                self.store.put('runtime','proxy_cleanup',{'pending':False},c)
        try:
            if self.config.controller_url:
                await deactivate_mihomo(self.config.controller_url,self.config.controller_secret,self.config.proxy_config_path,before_commit=commit)
            else:
                commit()
        except Exception:
            with self.store.connect() as c:
                cfg=self.store.settings(c)
                cfg['subscription']['status']='凭证已撤销；代理清理失败，将重试。请检查客户端状态'
                self.store.save_settings(cfg,c)
                self.store.put('runtime','proxy_cleanup',{'pending':True,'retry_at':time.time()+30},c)

    async def execute(self, task):
        s=self.store
        task['settings']=s.unseal(task['snapshot'])
        if task.get('checkpoint'):
            task['checkpoint_data']=s.unseal(task['checkpoint'])
        cfg=task['settings']
        def check(): self.check(task,('model','github','proxy') if task['kind']=='analysis' else ('github','proxy','subscription'))
        def event(kind,message): s.event(task['id'],kind,message)
        def save(checkpoint): self.update_task(task['id'],checkpoint=s.seal(checkpoint))
        try:
            check()
            proxy=proxy_for(cfg,self.config)
            if task['kind']=='analysis':
                async with self.github_factory(cfg['github']['token'],proxy) as github, self.model_factory(cfg['model']['base_url'],cfg['model']['api_key'],cfg['model']['model']) as model:
                    result,drafts=await run_agent(task,github,model,check,save,event)
                check()
                with s.connect() as c:
                    latest=s.get('tasks',task['id'],c)
                    if latest['status']=='cancelled': return
                    for index,draft in enumerate(drafts):
                        ident=task['id']+'-'+str(index)
                        draft.update(id=ident,task_id=task['id'],repository=task['repository'],version=1,status='draft',external_url=None,error=None)
                        if not s.get('drafts',ident,c): s.put('drafts',ident,draft,c)
                    latest.update(result=result,status='waiting_confirmation' if drafts else 'completed',error=None,updated_at=now())
                    s.put('tasks',task['id'],latest,c)
                    event_message='分析完成，草稿等待确认' if drafts else '分析完成'
                    s.event(task['id'],'result',event_message,c)
            elif task['kind']=='diagnostic':
                event('network','从 Worker 环境检查 GitHub 连接和公开仓库')
                async with self.github_factory(cfg['github']['token'],proxy) as github:
                    await github.diagnose(task['repository'])
                check()
                self.update_task(task['id'],status='completed',result={'report':'GitHub 连接测试成功。发布权限需要实际发布时再次校验。','evidence':[],'limitations':[]})
            elif task['kind']=='node':
                await self.subscription(task,proxy,check,event)
            else:
                raise IntegrationError('不支持的任务类型',kind='validation')
        except StopTask:
            return
        except IntegrationError as exc:
            status='waiting_rate_limit' if exc.kind=='rate_limit' else 'waiting_network' if exc.kind=='network' else 'failed'
            self.update_task(task['id'],status=status,error=str(exc),retry_at=exc.retry_at or 0)
            event('error',str(exc))
        except Exception:
            self.update_task(task['id'],status='failed',error='任务执行失败，检查服务配置后重试')
            event('error','任务执行失败（已隐藏远端敏感信息）')

    async def subscription(self,task,proxy,check,event):
        from .subscriptions import activate_mihomo
        cfg=task['settings'];s=self.store
        if (s.get('runtime','proxy_cleanup') or {}).get('pending'):
            raise IntegrationError('旧订阅代理仍待清理，请修复客户端后恢复任务',kind='network')
        nodes=cfg['subscription']['nodes']
        check()
        selected=task.get('payload',{}).get('name') or cfg['subscription']['selected_node']
        if selected not in [n['name'] for n in nodes]: selected=nodes[0]['name']
        event('subscription','测试候选代理配置，失败时保留原配置')
        def commit():
            check()
            with s.connect() as c:
                current=s.settings(c)
                latest=s.get('tasks',task['id'],c)
                if latest.get('status')=='cancelled':
                    raise StopTask()
                if (current['subscription']['url']!=cfg['subscription']['url'] or current['generations']['subscription']!=cfg['generations']['subscription'] or current['subscription']['nodes']!=cfg['subscription']['nodes']):
                    raise IntegrationError('订阅配置已改变，请重新更新',kind='validation')
                current['subscription'].update(nodes=nodes,selected_node=selected,updated_at=now(),status='已启用')
                current['network'].update(mode='proxy',proxy_url=self.config.sidecar_proxy_url)
                s.save_settings(current,c)
                s.audit('subscription_activated','subscription',c)
        await activate_mihomo(nodes,selected,self.config.controller_url,self.config.controller_secret,self.config.proxy_config_path,self.config.sidecar_proxy_url,before_commit=commit)
        self.update_task(task['id'],status='completed',result={'report':'订阅节点已验证并启用。','evidence':[],'limitations':[]})

    async def publish(self,draft,reconcile=False):
        s=self.store
        task=s.get('tasks',draft['task_id'])
        if not task: return
        cfg=s.settings()
        marker=f'<!-- repository-steward:{draft["operation_id"]} -->'
        try:
            if cfg['generations']['github']!=draft.get('credential_generation',0):
                raise IntegrationError('发布凭证已撤销',kind='auth')
            token=s.unseal(draft['credential'])['token']
            async with self.github_factory(token,proxy_for(cfg,self.config)) as github:
                if reconcile:
                    result=await github.find_operation(draft['repository'],marker,issue_number=draft.get('issue_number'))
                    if not result: return
                else:
                    # Recheck cancellation and credential revocation immediately before external write.
                    current=s.get('drafts',draft['id'])
                    if s.get('tasks',draft['task_id']).get('status')=='cancelled':
                        self.update_draft(draft['id'],status='discarded');return
                    if current['status']!='executing': return
                    if s.settings()['generations']['github']!=draft.get('credential_generation',0):
                        raise IntegrationError('发布凭证已撤销',kind='auth')
                    def guard():
                        if s.get('tasks',draft['task_id']).get('status')=='cancelled':
                            raise StopTask()
                        if s.settings()['generations']['github']!=draft.get('credential_generation',0):
                            raise IntegrationError('发布凭证已撤销',kind='auth')
                    result=await github.publish(draft['repository'],draft['kind'],draft['title'],draft['body']+'\n\n'+marker,draft.get('issue_number'),before_write=guard)
                url=result.get('html_url','')
                if not url.startswith('https://github.com/'+draft['repository']+'/issues/'):
                    raise IntegrationError('发布响应无法确认，请核实 GitHub',ambiguous=True)
                self.update_draft(draft['id'],status='succeeded',external_url=url,error=None)
                s.event(draft['task_id'],'published','已发布至 GitHub：'+url)
                s.audit('published',draft['id'])
                self.finish_if_resolved(draft['task_id'])
        except StopTask:
            self.update_draft(draft['id'],status='discarded',error='已取消，未发送发布请求')
        except IntegrationError as exc:
            if reconcile:
                self.update_draft(draft['id'],error='远端结果仍待核实；不会自动重复发布')
            elif exc.ambiguous:
                self.update_draft(draft['id'],status='unknown',error='发布结果不确定，正在只读核实；不会重复发布',next_check=time.time()+30)
            else:
                self.update_draft(draft['id'],status='failed',error=str(exc))
        except Exception:
            self.update_draft(draft['id'],status='unknown',error='发布结果待核实；不会重复发布',next_check=time.time()+30)

    def update_draft(self,ident,**fields):
        with self.store.connect() as c:
            d=self.store.get('drafts',ident,c)
            d.update(fields)
            self.store.put('drafts',ident,d,c)


async def main():
    config=Config();config.validate()
    worker=Worker(config)
    while True:
        await worker.tick()
        await asyncio.sleep(1)


if __name__=='__main__':
    asyncio.run(main())
