from chat.models import Message,Conversation
from django.db.models import Count
class ConversationService:
    @staticmethod
    def check_if_1o1_conversation_exist(sender_id,receiver_id):
        return Conversation.objects.annotate(
            total_participants=Count('participants')
        ).filter(
            total_participants=2,
            participants__id=sender_id,
            is_group=False
        ).filter(
            participants__id=receiver_id
        ).first()
    @staticmethod
    def create_conversation(*members):
        if members is None:
            return None
        length = len(members)
        if length == 1:
            return None
        is_group = length > 2
        conv = Conversation.objects.create(is_group=is_group)
        conv.participants.add(*members)
        return conv.id
    @staticmethod
    def send_message(user_id, conversation_id, message_content, user_1on1=-1):
        if conversation_id == -1:
            conv = ConversationService.check_if_1o1_conversation_exist(user_id, user_1on1)
            if not conv:
                conv = ConversationService.create_conversation(user_id,user_1on1)
            conversation_id = conv

        new_message = Message.objects.send_message(user_id, conversation_id, message_content)
        return new_message, conversation_id
    @staticmethod
    def load_user_conversations(user_id,page_number,page_size=300):
        try:
            ids = Conversation.objects.get_user_conversations(user_id, page_number, page_size)
            return ids
        except Exception as e:
            print(str(e))
            return []
    @staticmethod
    def load_conversation_messages(conversation_id,page_number,page_size=300):
        try:
            messages = Conversation.objects.get_conversation_messages_paged(conversation_id, page_number, page_size)
            return messages
        except Exception as e:
            print(str(e))
            return []