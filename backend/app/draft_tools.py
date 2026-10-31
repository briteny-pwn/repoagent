"""Model-callable preparation only. GitHub writes remain in confirmed publication."""
from urllib.parse import urlsplit
from .integrations import IntegrationError

DRAFT_FIELDS = {
    'prepare_issue': {
        'title': {'type':'string','minLength':1,'maxLength':256},
        'body': {'type':'string','minLength':1,'maxLength':12000,'description':'完整的 Markdown Issue 正文，简明描述背景、问题、建议与验收标准。'},
        'duplicate_query': {'type':'string','minLength':1,'maxLength':180,'description':'用于当前仓库查重的关键词，不包含仓库或类型限定符。'},
    },
    'prepare_comment': {
        'issue_number': {'type':'integer','minimum':1,'description':'用户指定或已明确确认的未关闭 Issue 编号。'},
        'body': {'type':'string','minLength':1,'maxLength':12000,'description':'完整的 Markdown 评论正文。'},
    },
}
DRAFT_DESCRIPTIONS = {
    'prepare_issue':'在当前项目准备一份 Issue 草稿。自动查重，供用户核对并确认发布；不会发布到 GitHub。成功后本次任务结束。',
    'prepare_comment':'在当前项目为指定未关闭 Issue 准备评论草稿。自动核实目标；不会发布到 GitHub。成功后本次任务结束，不支持 PR 评论。',
}


def invalid(message):
    raise IntegrationError(message, kind='draft_validation')


def required_text(args, key, limit):
    value=args.get(key)
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        invalid(f'{key} 必须是1至{limit}字符的非空文本，请精简后重新调用工具。')
    return value.strip()


def evidence(items, repo):
    result=[]
    for item in items[:5]:
        url=item.get('html_url')
        if not isinstance(url,str): continue
        parsed=urlsplit(url)
        if parsed.scheme=='https' and parsed.netloc=='github.com' and parsed.path.startswith('/'+repo+'/issues/'):
            result.append({'title':f'Issue #{item.get("number", "")}：{str(item.get("title") or "查重/评论目标")[:160]}','url':url})
    return result


async def prepare_draft(name, args, repo, github, check):
    if name not in DRAFT_FIELDS or not isinstance(args,dict) or set(args)!=set(DRAFT_FIELDS[name]):
        invalid('草稿工具参数不完整或包含未授权字段，请按工具定义重新调用。')
    body=required_text(args,'body',12000)
    if name=='prepare_issue':
        title=required_text(args,'title',256)
        query=required_text(args,'duplicate_query',180)
        # Keep the model-provided search focused on keywords, not alternative repositories/types.
        if ':' in query:
            invalid('duplicate_query 请只填写查重关键词，不包含 repo: 或 is: 等限定符。')
        check()
        found=await github.tool(repo,'search_issues',{'query':'is:issue '+query})
        check()
        if not isinstance(found,dict) or not isinstance(found.get('items'),list):
            raise IntegrationError('Issue 查重结果无效，未创建草稿。')
        candidates=[item for item in found['items'] if isinstance(item,dict)]
        draft={'kind':'issue','title':title,'body':body,'issue_number':None}
        return draft,{'report':'Issue 草稿已生成，尚未发布。请在下方核对内容和查重候选，确认后再发布。',
                      'evidence':evidence(candidates,repo),
                      'limitations':[f'本次关键词查重返回 {len(candidates)} 条候选，搜索可能受分页、索引延迟和用词影响，不保证没有重复 Issue。']}
    number=args.get('issue_number')
    if not isinstance(number,int) or isinstance(number,bool) or number<=0:
        invalid('issue_number 必须是明确的正整数 Issue 编号。')
    check()
    try:
        target=await github.tool(repo,'get_issue',{'number':number})
    except IntegrationError as exc:
        if exc.kind != 'not_found': raise
        check()
        await github.repository(repo)
        check()
        invalid('该 Issue 编号不存在或不可读取，请核实编号或向用户询问正确目标。')
    check()
    if not isinstance(target,dict) or target.get('number')!=number or 'pull_request' in target or target.get('state')!='open':
        invalid('评论目标必须是当前仓库中未关闭的 Issue，请核实编号；不支持 PR 评论。')
    draft={'kind':'comment','title':'','body':body,'issue_number':number}
    return draft,{'report':f'Issue #{number} 的评论草稿已生成，尚未发布。请在下方核对正文后确认发布。',
                  'evidence':evidence([target],repo),
                  'limitations':['目标已在准备时核实，发布前会再次检查其公开状态与是否关闭。']}
