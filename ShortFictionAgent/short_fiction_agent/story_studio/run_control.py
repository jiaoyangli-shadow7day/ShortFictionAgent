"""Persistent per-project limits and explicit stop state across CLI resumes."""
from contextlib import contextmanager
import fcntl
from .contracts import StoryError
from .storage import read_json, write_json, now

PROTOCOL = 'story-run-control-2026-10-09-v1'

DEFAULT_CALL_LIMIT = 40
# Resource ceiling for the default chapter writer/reader path. Thirty chapters
# need sixty calls before planning, lead selection, and final review. This is
# permitted capacity, never an instruction to make extra calls or a quality rule.
CHAPTER_STORY_CALL_LIMIT = 120


def default_limit(project):
    contract = project.contract()
    if (contract.get('format') == 'short_story'
            and contract.get('review_mode', 'chief_editor') == 'chief_editor'):
        return CHAPTER_STORY_CALL_LIMIT
    return DEFAULT_CALL_LIMIT


def load(project, limit=None):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise StoryError('模型调用预算必须是正整数')
    value = read_json(project.root / 'run_control.json')
    if value is None:
        limit = default_limit(project) if limit is None else limit
        return {'protocol': PROTOCOL, 'limit': limit, 'used': 0,
                'automatic_repairs': 0, 'format_repairs': 0, 'stopped': False}
    if (value.get('protocol') != PROTOCOL or type(value.get('used')) is not int
            or type(value.get('limit')) is not int or value['used'] < 0
            or value['limit'] < 1 or type(value.get('stopped')) is not bool
            or any(type(value.get(k, 0)) is not int or value.get(k, 0) < 0
                   for k in ('automatic_repairs', 'format_repairs'))):
        raise StoryError('运行预算记录损坏，不能重新取得额度')
    return value

@contextmanager
def _locked(project):
    project.root.mkdir(parents=True, exist_ok=True)
    with (project.root / '.run-control.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def initialise(project, limit=None):
    """Resolve a finite budget; explicit smaller limits may only tighten it.

    A larger argument never grants fresh capacity to an existing project. That
    still requires authorize_continue. Defaults leave historical ledgers intact.
    """
    if limit is None:
        return load(project)
    with _locked(project):
        state = load(project, limit)
        if limit < state['limit']:
            previous = state['limit']
            state.update(limit=limit, updated_at=now())
            write_json(project.root / 'run_control.json', state)
            project.record_run({'event': 'call_budget_tightened',
                                'previous_limit': previous, 'limit': limit, 'used': state['used']})
        return state


def _mutate(project, change, limit=None):
    with _locked(project):
        state = load(project, limit)
        result = change(state)
        state['updated_at'] = now()
        write_json(project.root / 'run_control.json', state)
        return state if result is None else result

def reserve(project, limit=None):
    def change(state):
        if state.get('stopped'):
            raise StoryError('本篇已停止；不会新增模型调用，可导出当前稿')
        if state['used'] >= state['limit']:
            raise StoryError('本篇累计模型调用预算已用尽；当前稿与未解决项已保留')
        state['used'] += 1
    return _mutate(project, change, limit)

def reserve_repair(project, limit=None):
    return _reserve_once(project, 'automatic_repairs', limit)

def reserve_format_repair(project, limit=None):
    return _reserve_once(project, 'format_repairs', limit)

def _reserve_once(project, field, limit):
    def change(state):
        if state.get('stopped') or state.get(field, 0) >= 1:
            return False
        state[field] = state.get(field, 0) + 1
        return True
    return _mutate(project, change, limit)

def stop(project):
    state = _mutate(project, lambda state: state.update(stopped=True))
    project.record_run({'event': 'author_stopped', 'used': state['used']})
    return state

def authorize_continue(project, *, extra_calls=0, repairs=False):
    if type(extra_calls) is not int or extra_calls < 0:
        raise StoryError('追加调用额度须为非负整数')
    def change(state):
        state.update(stopped=False, limit=state['limit'] + extra_calls)
        if repairs:
            state['automatic_repairs'] = 0
            state['format_repairs'] = 0
    state = _mutate(project, change)
    project.record_run({'event': 'author_continued', 'extra_calls': extra_calls,
                        'authorize_one_repair': repairs})
    return state
