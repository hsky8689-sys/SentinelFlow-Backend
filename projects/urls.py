from django.urls import path

from projects.views import open_project_page, open_project_members_page, open_project_settings, \
    api_project_domains, api_project_requirements, api_project_requirement_sections, api_project_tasks, \
    api_project_roles, \
    github_proxy_view, proxy_run_code, push_files, \
    api_get_availible_languages, api_request_project_join, api_handle_project_join_request, request_file_open, \
    api_handle_file_access_request, api_request_file_share, api_handle_request_file_share, \
    api_github_get_all_repo_branches, api_github_handle_branch_action, api_merge_github_branches, webhook_github, \
    api_handle_project_repositories, api_project_push_policy, \
    api_invite_to_project, api_handle_project_invite, api_leave_project

app_name = 'projects'

urlpatterns = [
    path("project-page/<slug:name>",open_project_page,name="project-page"),
    path("project-page/<slug:name>/project-members",open_project_members_page,name="project-members"),
    path("project-page/<slug:name>/settings",open_project_settings,name="project-settings"),
    path("settings/<int:id>/domains", api_project_domains, name="project-domains"),
    path("settings/<int:id>/requirements", api_project_requirements, name="project-requirements"),
    path("settings/<int:id>/requirement-sections", api_project_requirement_sections, name="project-requirement-sections"),
    path("settings/<int:id>/tasks", api_project_tasks, name="project-tasks"),
    path("settings/<int:id>/roles", api_project_roles, name="project-roles"),
    path("settings/<int:id>/push-policy", api_project_push_policy, name="project-push-policy"),
    path('api/github/branches',api_github_get_all_repo_branches,name='get-all-repo-branches'),
    path('api/github/<int:id>/branches',api_github_handle_branch_action,name='add-branch-on-github-repo'),
    path('api/github/<str:owner>/<str:repo>',github_proxy_view,name='github-fetch-structure'),
    path('api/github/<str:owner>/<str:repo>/<path:path>',github_proxy_view,name='github-fetch-path'),
    path('api/code',proxy_run_code,name='run-code'),
    path('api/github/pushed-files',push_files,name='push-code'),
    path('api/file-access',request_file_open,name='request-file-access'),
    path('available-languages',api_get_availible_languages,name="view-selected-languages"),
    path('api/<int:project_id>/request-join',api_request_project_join,name='send-project-join-request'),
    path('api/requests/project/handle',api_handle_project_join_request,name='handle-project-join-request'),
    path('settings/<int:id>/invitation',api_invite_to_project,name='invite-to-project'),
    path('invites/<int:invite_id>',api_handle_project_invite,name='handle-project-invite'),
    path('settings/<int:id>/project-exit',api_leave_project,name='leave-project'),
    path('api/requests/file-access/handle',api_handle_file_access_request,name='handle-file-access-request'),
    path('api/requests/file-writers',api_request_file_share,name='request-file-share'),
    path('api/requests/file-writers/handle',api_handle_request_file_share,name='handle-request-file-share'),
    path('api/github/<int:id>/merges',api_merge_github_branches,name='merge-branches'),
    path('api/<int:id>/github-webhook',webhook_github,name='webhook-github'),
    path('api/<int:id>',api_handle_project_repositories,name='add-repo-to-project'),
]


