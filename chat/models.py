from datetime import datetime, timezone
from django.db import models
from django.utils import timezone
from users.models import User
class ConversationManager(models.Manager):
    def get_user_conversations(self,user_id,page_number=1,page_size=100):
        offset = page_number * page_size
        conversations = self.filter(
            participants=user_id
        ).order_by('-last_message_timestamp')[offset: offset + page_size]
        return list(conversations)

    def get_conversation_messages_paged(self, conversation_id, page_number=1,page_size=1000):
        offset = page_number * page_size
        messages = Message.objects.filter(conversation_id=conversation_id).order_by('-timestamp')[offset: offset + page_size]
        messages = list(messages)
        # Transformarea în listă de obiecte evaluate (forțează interogarea în DB acum)
        return messages

class MessagesManager(models.Manager):
    def send_message(self,user_id:int,conversation_id: int, message_content: str):
        new_message = Message(
            conversation_id=conversation_id,
            user_id=user_id,
            content=message_content
        )
        new_message.save()
        Conversation.objects.filter(id=conversation_id).update(
            last_message_timestamp=timezone.now()
        )
        return new_message
class Conversation(models.Model):
    creation_timestamp = models.DateTimeField(
        default=timezone.now,
    )
    last_message_timestamp = models.DateTimeField(
        default=timezone.now,
        db_index=True
    )
    is_group = models.BooleanField(
        default=False
    )
    participants = models.ManyToManyField(User, related_name='participated_conversations')
    objects = ConversationManager()
    class Meta:
        db_table='conversations'
class Message(models.Model):
    user = models.ForeignKey(User,
                        on_delete=models.CASCADE
                        ,related_name='messages'
                    )
    conversation = models.ForeignKey(Conversation,
                        on_delete=models.CASCADE,
                        related_name='conversations',
                        default=-1
                    )
    content = models.CharField(max_length=10000)
    timestamp = models.DateTimeField(default=timezone.now)
    image = models.ImageField(blank=True,null=True)
    objects = MessagesManager()
    class Meta:
        db_table='messages'
        indexes = [
            models.Index(fields=['conversation', 'timestamp'], name='msg_conv_timestamp_idx')
        ]