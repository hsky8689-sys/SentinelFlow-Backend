import json
from channels.generic.websocket import AsyncWebsocketConsumer

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.owner = self.scope['url_route']['kwargs']['owner']
        self.repo = self.scope['url_route']['kwargs']['repo']
        self.room_group_name = f'chat_{self.owner}_{self.repo}'

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
    async def disconnect(self, code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    async def receive(self, text_data=None, bytes_data=None):
        data = json.loads(text_data)
        message = data.get('message')
        user = self.scope["user"].username if self.scope["user"].is_authenticated else "Unknown"
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type":"chat_message",
                "message":message,
                "user":user
            }
        )
    async def chat_message(self,event):
        message = event['message']
        user = event['user']

        await self.send(text_data=json.dumps({
            'type': 'CHAT_MESSAGE',
            'message': message,
            'user': user
        }))