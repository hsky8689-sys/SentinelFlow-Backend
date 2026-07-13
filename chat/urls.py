"""
URL configuration for devnetwork project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path

from chat import views
app_name = "chat"
urlpatterns = [
    path('', views.open_chat_room, name='chat_room'),
    path("api/message", views.chat_message_api, name="send_message"),
    path("api/<int:conversation_id>",views.load_chat_by_id,name="load_chat"),
    path("conversations",views.load_user_conversations,name="load_conversations"),
    path("conversations/projects/<int:project_id>",views.api_project_conversations,name="project_conversations")
]
