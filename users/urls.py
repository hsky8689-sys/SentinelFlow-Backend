from django.urls import path

from projects.views import api_request_file_access
from users.views import signup_page, acces_profile, api_add_skill, api_delete_skill, search_page, \
    search_api, create_project, api_friend_requests, connections_page, api_friend_request_detail, \
    api_remove_friend, logout_page, provide_csrf_token, api_add_techstack_section, api_delete_techstack_section, \
    api_add_profile_section, api_handle_profile_section, api_handle_profile_picture_upload, \
    api_add_background_picture

app_name = 'users'

urlpatterns = [
    path("signup",signup_page),
    path("logout",logout_page,name="logout"),
    path('skills',api_add_skill,name='api_add_skill'),
    path('skills/<int:skill_id>',api_delete_skill,name='api_delete_skill'),
    path('techstacks/',api_add_techstack_section),
    path('techstacks/<int:section_id>',api_delete_techstack_section,name='api_delete_techstack_section'),
    path('profile-sections/',api_add_profile_section,name='api_add_profile_section'),
    path('profile-sections/<int:section_id>',api_handle_profile_section,name='api_handle_profile_section'),
    path('profile-pictures/',api_handle_profile_picture_upload),
    path('background-pictures/',api_add_background_picture),

    path('search', search_page, name='search_page'),
    path('search/api', search_api, name='search_api'),
    path('create-new-project',create_project,name='create_project'),
    path('connections-page',connections_page,name='view_connections'),
    path('api/projects/<int:project_id>/request-file', api_request_file_access, name='request_file_access'),
    path('friend-requests',api_friend_requests,name='friend-requests'),
    path('friend-requests/<int:id>',api_friend_request_detail,name='friend-request-detail'),
    path('<int:removed>/friendship',api_remove_friend,name='remove_friend'),
    # must stay last: a bare <slug:username>/ would otherwise swallow every literal
    # single-segment path above it (skills/, search/, friend-requests/, etc.)
    path('<slug:username>',acces_profile,name="profile-path"),
    path('api/csrf-token',provide_csrf_token)
]