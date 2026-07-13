import hashlib
import hmac
import json
from datetime import datetime

import requests
from django.db import transaction
from django.http import JsonResponse

from devnetwork import settings
from devnetwork.caching import cache_manager, ProjectCacheKey
from projects.models import Project, UserProjectRole, ProjectRepoStats


def register_github_webhook(owner, repo, project, token):
    """
    TODO raw scaffold: registers a 'push' webhook on the given repo, signed with
    the project's own app_signing_key (reused here as the webhook secret too).
    """
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/hooks"
        payload = {
            "name": "web",
            "active": True,
            "events": ["push"],
            "config": {
                "url": settings.GITHUB_WEBHOOK_CALLBACK_URL,  # TODO: must be a public URL (ngrok in dev)
                "content_type": "json",
                "secret": project.app_signing_key,
                "insecure_ssl": "0"
            }
        }
        response = requests.post(url, headers=headers, json=payload)
        return response.status_code == 201
    except Exception as e:
        print(str(e))
        return False
def get_github_username(token):
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        response = requests.get("https://api.github.com/user", headers=headers)
        if response.status_code == 200:
            return response.json().get('login')
        return None
    except Exception as e:
        print(str(e))
        return None
def apply_branch_protection(owner, repo, token, repo_stat):
    """
    TODO raw scaffold: restricts push access on the repo's default branch to
    only the GitHub account behind `token`, saving whatever protection existed
    before (if any) on `repo_stat` so it can be restored with revert_branch_protection.

    Caveat: if `token` belongs to a personal account (not a dedicated bot/service
    account), that same person can still push manually with their own git
    credentials - this only blocks *other* collaborators, not the token's own owner.
    """
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        branch = get_default_branch(owner, repo)
        if not branch:
            return False
        url = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}/protection"

        existing = requests.get(url, headers=headers)
        previous_protection = existing.json() if existing.status_code == 200 else None

        app_username = get_github_username(token)
        if not app_username:
            return False

        payload = {
            "required_status_checks": None,
            "enforce_admins": None,
            "required_pull_request_reviews": None,
            "restrictions": {
                "users": [app_username],
                "teams": [],
                "apps": []
            }
        }
        response = requests.put(url, headers=headers, json=payload)
        if response.status_code not in (200, 201):
            return False

        repo_stat.protected_branch = branch
        repo_stat.previous_branch_protection = json.dumps(previous_protection) if previous_protection else ''
        repo_stat.save(update_fields=['protected_branch', 'previous_branch_protection'])
        return True
    except Exception as e:
        print(str(e))
        return False
def revert_branch_protection(repo_stat):
    """
    TODO raw scaffold: undoes apply_branch_protection. If the branch had no
    protection before we touched it, removes protection entirely. Otherwise,
    best-effort reconstructs a PUT payload from the previously stored GET
    response - GitHub's GET/PUT shapes for branch protection don't match 1:1
    (e.g. users/teams come back as full objects, not login/slug strings), so
    this only round-trips the common fields (restrictions, required reviews,
    required status checks, enforce_admins). Anything more exotic
    (required_signatures, lock_branch, allow_force_pushes, etc.) needs separate
    endpoints and isn't restored here.
    """
    try:
        owner, repo = get_project_owner_repo_from_link(repo_stat.github_repo_link)
        if not owner or not repo or not repo_stat.protected_branch:
            return True
        headers = {"Accept": "application/vnd.github+json"}
        if repo_stat.github_token:
            headers["Authorization"] = f"token {repo_stat.github_token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/branches/{repo_stat.protected_branch}/protection"

        if not repo_stat.previous_branch_protection:
            response = requests.delete(url, headers=headers)
            success = response.status_code == 204
        else:
            previous = json.loads(repo_stat.previous_branch_protection)
            restrictions = previous.get('restrictions')
            payload = {
                "required_status_checks": {
                    "strict": previous.get('required_status_checks', {}).get('strict', False),
                    "contexts": previous.get('required_status_checks', {}).get('contexts', [])
                } if previous.get('required_status_checks') else None,
                "enforce_admins": previous.get('enforce_admins', {}).get('enabled', False) if previous.get('enforce_admins') else None,
                "required_pull_request_reviews": {
                    "required_approving_review_count": previous.get('required_pull_request_reviews', {}).get('required_approving_review_count', 1)
                } if previous.get('required_pull_request_reviews') else None,
                "restrictions": {
                    "users": [u['login'] for u in restrictions.get('users', [])],
                    "teams": [t['slug'] for t in restrictions.get('teams', [])],
                    "apps": [a['slug'] for a in restrictions.get('apps', [])]
                } if restrictions else None
            }
            response = requests.put(url, headers=headers, json=payload)
            success = response.status_code in (200, 201)

        if success:
            repo_stat.protected_branch = ''
            repo_stat.previous_branch_protection = ''
            repo_stat.save(update_fields=['protected_branch', 'previous_branch_protection'])
        return success
    except Exception as e:
        print(str(e))
        return False
def get_project_owner_repo_from_link(github_repo_link):
    link_parts = github_repo_link.split('/')
    if len(link_parts) < 5:
        return None, None
    return link_parts[3], link_parts[4]
def get_project_repo_summaries(project):
    """
    Returns the project's linked repos as [{id, name, owner, repo, link}, ...],
    cached as one structure per project (not split into parallel owner/url lists).
    Distinct from ProjectRepoStats.objects.get_project_repos, which returns the
    raw (uncached) queryset of ProjectRepoStats model instances.
    """
    cache_key = ProjectCacheKey.REPOS.format(project_id=project.id)
    repos = cache_manager.get(cache_key)
    if repos is None:
        repos = []
        for stat in project.repo_stats.all():
            stat_owner, stat_repo = get_project_owner_repo_from_link(stat.github_repo_link)
            repos.append({
                'id': stat.id,
                'name': stat.github_repo_name,
                'owner': stat_owner,
                'repo': stat_repo,
                'link': stat.github_repo_link,
            })
        cache_manager.set(cache_key, repos, timeout=3600)
    return repos
def _add_project_repository(request,id):
    try:
        project = Project.objects.filter(id=id).first()
        if project is None:
            return JsonResponse({'status': 'error', 'message': 'Project not found'}, status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project,request.user)
        if not UserProjectRole.objects.get_role_permissions(role,project)['can_change_project_settings']:
            return JsonResponse({'status':'Unauthorized access'},status=403)
        data = json.loads(request.body)
        github_repo_name = data.get('github_repo_name')
        github_repo_link = data.get('github_repo_link')
        github_token = data.get('github_token', '')
        if not all([github_repo_name,github_repo_link]):
            return JsonResponse({'status':'bad request',
                                      'message':'github_repo_name and github_repo_link are required'},status=400)
        repo_stat = ProjectRepoStats.objects.create(github_repo_name=github_repo_name,github_repo_link=github_repo_link,github_token=github_token)
        project.repo_stats.add(repo_stat)
        cache_manager.delete(ProjectCacheKey.REPOS.format(project_id=project.id))
        owner, repo = get_project_owner_repo_from_link(github_repo_link)
        webhook_registered = False
        branch_protection_applied = False
        if owner and repo:
            webhook_registered = register_github_webhook(owner, repo, project, github_token)
            if not webhook_registered:
                print(f"Could not register github webhook for {owner}/{repo}")
            if project.can_only_modify_from_app:
                branch_protection_applied = apply_branch_protection(owner, repo, github_token, repo_stat)
                if not branch_protection_applied:
                    print(f"Could not apply branch protection for {owner}/{repo}")
        return JsonResponse({'status':'success','repo_id':repo_stat.id,
                             'webhook_registered':webhook_registered,
                             'branch_protection_applied':branch_protection_applied},status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'},status=500)
def _delete_project_repository(request,id):
    try:
        project = Project.objects.filter(id=id).first()
        if project is None:
            return JsonResponse({'status': 'error', 'message': 'Project not found'}, status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project,request.user)
        if not UserProjectRole.objects.get_role_permissions(role,project)['can_change_project_settings']:
            return JsonResponse({'status':'Unauthorized access'},status=403)
        data = json.loads(request.body)
        repo_id = data.get('repo_id')
        if not repo_id:
            return JsonResponse({'status':'bad request','message':'repo_id is required'},status=400)
        repo_stat = project.repo_stats.filter(id=repo_id).first()
        if repo_stat is None:
            return JsonResponse({'status':'bad request','message':'repository not linked to this project'},status=404)
        if repo_stat.protected_branch:
            revert_branch_protection(repo_stat)
        project.repo_stats.remove(repo_stat)
        cache_manager.delete(ProjectCacheKey.REPOS.format(project_id=project.id))
        return JsonResponse({'status':'success','message':'Repository removed from project'},status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'},status=500)
def _get_project_push_policy(request,id):
    try:
        project = Project.objects.filter(id=id).first()
        if project is None:
            return JsonResponse({'status': 'error', 'message': 'Project not found'}, status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if role == 'visitor':
            return JsonResponse({'status': 'error', 'message': 'You are not a member of this project'}, status=403)
        return JsonResponse({
            'status': 'success',
            'can_only_modify_from_app': project.can_only_modify_from_app,
            'flagged_external_push': project.flagged_external_push,
        }, status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _set_project_push_policy(request,id):
    try:
        project = Project.objects.filter(id=id).first()
        if project is None:
            return JsonResponse({'status': 'error', 'message': 'Project not found'}, status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project,request.user)
        if not UserProjectRole.objects.get_role_permissions(role,project)['can_change_project_settings']:
            return JsonResponse({'status':'Unauthorized access'},status=403)
        data = json.loads(request.body)
        enabled = data.get('can_only_modify_from_app')
        if enabled is None:
            return JsonResponse({'status':'bad request','message':'can_only_modify_from_app is required'},status=400)
        enabled = bool(enabled)
        repo_results = []
        for repo_stat in project.repo_stats.all():
            owner, repo = get_project_owner_repo_from_link(repo_stat.github_repo_link)
            if not owner or not repo:
                continue
            if enabled:
                success = apply_branch_protection(owner, repo, repo_stat.github_token, repo_stat)
            else:
                success = revert_branch_protection(repo_stat)
            repo_results.append({'repo_id': repo_stat.id, 'success': success})
        project.can_only_modify_from_app = enabled
        project.save(update_fields=['can_only_modify_from_app'])
        return JsonResponse({
            'status': 'success',
            'can_only_modify_from_app': enabled,
            'repos': repo_results,
        }, status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _clear_flagged_external_push(request,id):
    try:
        project = Project.objects.filter(id=id).first()
        if project is None:
            return JsonResponse({'status': 'error', 'message': 'Project not found'}, status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project,request.user)
        if not UserProjectRole.objects.get_role_permissions(role,project)['can_change_project_settings']:
            return JsonResponse({'status':'Unauthorized access'},status=403)
        project.flagged_external_push = False
        project.save(update_fields=['flagged_external_push'])
        return JsonResponse({'status': 'success', 'flagged_external_push': False}, status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def get_project_owner_repo(project):
    repo_stat = project.repo_stats.first()
    if repo_stat is None:
        return None, None
    root_link_parts = repo_stat.github_repo_link.split('/')
    if len(root_link_parts) < 5:
        return None, None
    return root_link_parts[3], root_link_parts[4]

def get_project_repo_token(project):
    repo_stat = project.repo_stats.first()
    return repo_stat.github_token if repo_stat else None

def get_repo_token(owner, repo):
    repo_stat = ProjectRepoStats.objects.filter(github_repo_link__icontains=f'{owner}/{repo}').first()
    return repo_stat.github_token if repo_stat else None

def user_has_access_to_github_repo(user, owner, repo):
    """
    True if `user` holds a role (any role - UserProjectRole rows only exist
    for actual members, 'visitor' is just the fallback for "no row found")
    in at least one project that links this owner/repo.

    github_proxy_view/handle_file_content take owner/repo straight from the
    URL path with no project_id at all, so this is the only thing standing
    between "logged in to the app" and "can read this repo's full tree and
    file contents" - including private repos, since get_repo_token attaches
    whatever token is stored for the repo regardless of who's asking.
    """
    member_project_ids = UserProjectRole.objects.filter(user=user).values_list('project_id', flat=True)
    return ProjectRepoStats.objects.filter(
        github_repo_link__icontains=f'{owner}/{repo}',
        projects__id__in=member_project_ids,
    ).exists()

def fetch_github_tree_with_sizes(owner, repo, branch='main'):
    """
    Fetches the recursive git tree from GitHub, keyed by path, including each
    blob's size so callers can detect when a cached tree has gone stale.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = get_repo_token(owner, repo)
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    response = requests.get(url, headers=headers)

    if response.status_code == 404 and branch == 'main':
        branch = 'master'
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return {}, branch

    raw_tree = response.json().get('tree', [])
    tree_by_path = {
        item['path']: {
            'path': item['path'],
            'type': 'dir' if item['type'] == 'tree' else 'file',
            'size': item.get('size', 0),
        }
        for item in raw_tree
    }
    return tree_by_path, branch


def get_project_tree_paths(project, branch='main'):
    """
    Returns the set of every file/folder path that exists in the project's
    github repo, reading from the same redis cache used by github_proxy_view.
    If the tree isn't cached yet, it's fetched from the GitHub API and cached.
    """
    repo_stat = project.repo_stats.first()
    if repo_stat is None:
        return set()
    root_link_parts = repo_stat.github_repo_link.split('/')
    if len(root_link_parts) < 5:
        return set()
    owner, repo = root_link_parts[3], root_link_parts[4]

    cache_key = f"github_tree_recursive_{owner}_{repo}_{branch}"
    tree = cache_manager.get(cache_key)
    if tree:
        return {item['path'] for item in tree}

    headers = {"Accept": "application/vnd.github.v3+json"}
    if repo_stat.github_token:
        headers["Authorization"] = f"token {repo_stat.github_token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    response = requests.get(url, headers=headers)

    if response.status_code == 404 and branch == 'main':
        branch = 'master'
        cache_key = f"github_tree_recursive_{owner}_{repo}_{branch}"
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return set()

    raw_tree = response.json().get('tree', [])
    formatted_tree = [{
        'name': item['path'].split('/')[-1],
        'path': item['path'],
        'type': 'dir' if item['type'] == 'tree' else 'file'
    } for item in raw_tree]
    cache_manager.set(cache_key, formatted_tree, timeout=3600)
    return {item['path'] for item in formatted_tree}

def invalidate_repo_cache(repo:str,owner:str,branch:str):
    """
    Invalidates every cached entry for the exact branch that was just pushed to:
    its recursive tree listing and every per-file/sub-folder content cache for
    that branch, so a push is immediately reflected instead of serving stale
    cached structure/content on the next request. Scoped to `branch` specifically
    (not hardcoded to 'main'/'master') since a push can target any branch name.
    """
    try:
        cache_manager.delete(f"github_tree_recursive_{owner}_{repo}_{branch}")
        cache_manager.delete(f"github_tree_with_size_{owner}_{repo}_{branch}")
        cache_manager.delete_pattern(f"github_file_{owner}_{repo}_{branch}_*")
        cache_manager.delete_pattern(f"file_content_{owner}_{repo}_{branch}_*")
    except Exception as e:
        print(str(e))
def is_repo_private(owner,repo):
    try:
        headers = {
            "Accept": "application/vnd.github+json"
        }
        token = get_repo_token(owner, repo)
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data['visibility'] == 'private'
    except Exception as e:
        print(str(e))
def get_default_branch(owner,repo):
    try:
        headers = {
            "Accept": "application/vnd.github+json"
        }
        token = get_repo_token(owner, repo)
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('default_branch')
        return None
    except Exception as e:
        print(str(e))
        return None
def get_branch_sha(owner,repo,branch_name):
    try:
        headers = {
            "Accept": "application/vnd.github+json"
        }
        token = get_repo_token(owner, repo)
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch_name}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()['object']['sha']
        return None
    except Exception as e:
        print(str(e))
        return None
def get_all_github_repo_branches(owner,repo):
    try:
        with transaction.atomic():
            url = f'https://api.github.com/repos/{owner}/{repo}/branches'
            headers = {}
            token = get_repo_token(owner, repo)
            if token:
                headers["Authorization"] = f"token {token}"
            meta_res = requests.get(url, headers=headers) if is_repo_private(owner,repo) else requests.get(url)
            if meta_res.ok:
                branches_json = meta_res.json()
                branches = [br['name'] for br in branches_json]
                return branches
            else:
                return []
    except Exception as e:
        print(str(e))
        return []
def add_new_branch_to_repo(project,new_branch_name=None):
    try:
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status': 'bad request',
                                      'message': 'Internal server error'}, status=403)
        new_sync_branch = new_branch_name
        if new_branch_name is None:
            now = str(datetime.now()).replace(' ', '__').replace(':', '-').replace('.', '')
            new_sync_branch = f'Branch_created_w_Sentinel_Flow_at_{now}' if new_branch_name is None else new_branch_name
        default_branch_name = get_default_branch(owner,repo)
        master_branch_sha = get_branch_sha(owner,repo,default_branch_name)
        headers = {"Accept": "application/vnd.github+json"}
        token = get_project_repo_token(project)
        if token:
            headers["Authorization"] = f"token {token}"
        data = {
            "ref": f'refs/heads/{new_sync_branch}',
            "sha": master_branch_sha
        }
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs"
        response = requests.post(url,headers=headers,json=data)
        if response.status_code != 201:
            return JsonResponse({'status': 'bad request','message':response.json()},status=402)
        return JsonResponse({'status':'success',
                             'messaage':f'Succesfully added branch {new_sync_branch}'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error','message': 'Internal server error'},status=500)
def modify_branch_from_repo(project,data):
    try:
        old_name = data.get('branch_name')
        new_name = data.get('new_name')
        if not old_name or not new_name:
            return JsonResponse({'status':'bad request','message':'branch_name and new_name are required'},status=400)
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status': 'bad request','message': 'Internal server error'}, status=403)
        headers = {"Accept": "application/vnd.github+json"}
        token = get_project_repo_token(project)
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/branches/{old_name}/rename"
        response = requests.post(url,headers=headers,json={"new_name":new_name})
        if response.status_code != 201:
            return JsonResponse({'status':'bad request','message':response.json()},status=402)
        return JsonResponse({'status':'success','message':f'Branch {old_name} renamed to {new_name}'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def delete_branch_from_repo(project,data):
    try:
        branch_name = data.get('branch_name')
        if not branch_name:
            return JsonResponse({'status':'bad request','message':'branch_name is required'},status=400)
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status': 'bad request','message': 'Internal server error'}, status=403)
        default_branch_name = get_default_branch(owner,repo)
        if branch_name == default_branch_name:
            return JsonResponse({'status':'bad request','message':'Cannot delete the default branch'},status=400)
        headers = {"Accept": "application/vnd.github+json"}
        token = get_project_repo_token(project)
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch_name}"
        response = requests.delete(url,headers=headers)
        if response.status_code != 204:
            return JsonResponse({'status':'bad request','message':response.json() if response.content else 'Could not delete branch'},status=402)
        return JsonResponse({'status':'success','message':f'Branch {branch_name} deleted'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def verify_github_signature(payload_body, signature_header, secret):
    """TODO raw scaffold: confirms this HTTP request really came from GitHub."""
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
def commit_was_pushed_from_app(commit, secret):
    """
    TODO raw scaffold: looks for our HMAC trailer inside the commit message to
    prove the commit content was produced by our backend (push_files), not a
    direct git push / GitHub UI edit. Trailer format assumed: '\\n\\nX-GitSync-Sig: <hex>'
    """
    message = commit.get('message', '')
    marker = 'X-GitSync-Sig:'
    if marker not in message:
        return False
    body, _, tag = message.rpartition(marker)
    tag = tag.strip()
    expected_tag = hmac.new(secret.encode('utf-8'), body.strip().encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_tag, tag)