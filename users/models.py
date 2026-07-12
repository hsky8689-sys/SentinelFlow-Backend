import re
import django.db
from django.core.validators import validate_slug
from django.db.models import Q
from django.db import models,transaction
from django.contrib.auth.models import AbstractBaseUser,BaseUserManager,PermissionsMixin
from django.utils import timezone
from django.db import transaction

from devnetwork.caching import cache_manager, UserCacheKey

class CustomUserManager(BaseUserManager):
    def create_user(self,username,email,password,birthday):
        try:
            with transaction.atomic():
                user = self.model(username=username, email=email, birthday=birthday)
                user.set_password(password)
                user.save(using=self._db)
                UserProfileSection.objects.create_default_user_sections(user.id)
                UserTechnicalSkillSection.objects.create_user_default_techstack(user.id)
                return user
        except Exception as e:
            print(str(e))
            return None

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, email, password, **extra_fields)
    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD:username})

class User(AbstractBaseUser,PermissionsMixin):
    username = models.CharField(max_length=100, blank=False, unique=True, validators=[validate_slug])
    login_date = models.DateTimeField(default=timezone.now)
    email = models.CharField(max_length=100, blank=False, unique=True)
    birthday = models.DateField(null=True,blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    objects = CustomUserManager()
    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email','password']
    class Meta:
        db_table = 'users'
        #managed = False

class UserProfileDataManager(models.Manager):
    def get_profile_data(self, user):
        cache_key = UserCacheKey.PROFILE_DATA.format(user_id=user.id)
        data = cache_manager.get(cache_key)
        if data is None:
            data = self.filter(user_id=user.id).first()
            cache_manager.set(cache_key, data, timeout=3600)
        return data

class UserProfileData(models.Model):
    user = models.ForeignKey(User,on_delete=models.CASCADE)
    objects = UserProfileDataManager()
    profile_picture = models.ImageField(
        upload_to='static/profile_pictures/%Y/%m/%d/',
        blank=True,
        null=True,
        default='static/profile_pictures/sbcf-default-avatar.png'
    )
    background_picture = models.ImageField(
        upload_to='static/background_pictures/%Y/%m/%d/',
        blank=True,
        null=True,
        default='static/background_pictures/sbcf-default-backgrounds.png'
    )
    biography = models.CharField(max_length=200,blank=False,default="Welcome to my profile!")
    class Meta:
        db_table = 'profile_datas'
class CustomUserProfileSectionManager(models.Manager):
    def create_user_profile_section(self,user:User,name:str,content:str,hidden:bool):
        """
        Creates a new profile section with no which will be added to an user's personal page
        :param user: The specified user
        :param name: The new section's name (Non-empty at least 100 characters)
        :param content: The new section's content (Non-empty at least 100 characters)
        :param hidden: States if the section will be or not hidden to foreign profile visitors
        :return: None
        """
        new_section = (self.create(user=user,
                               name=name,
                               content=content,
                               hidden=hidden
                               ))
        new_section.save()
        cache_manager.delete(UserCacheKey.PROFILE_SECTIONS.format(user_id=user.id))
    def delete_user_profile_section(self,user:User,section_id):
        """
        Deletes a former profile section from an user's personal page
        :param user:
        :return:true or false if the section was updated accordingly
        """
        self.filter(id=section_id,user_id=user.id).delete()
        cache_manager.delete(UserCacheKey.PROFILE_SECTIONS.format(user_id=user.id))
        return self.filter(id=section_id).count() == 0

    def update_user_profile_section(self,new_section)-> bool | None:
        """
        Updates a user's profile section
        :param user:
        :return: true or false if the section was updated accordingly
        """
        try:
            with transaction.atomic():
                former_section = UserProfileSection.objects.select_for_update().filter(id=new_section.id)
                if former_section is None:
                    return False
                owner_user_id = former_section.values_list('user_id', flat=True).first()
                former_section.update(name=new_section.name,content=new_section.content,hidden=new_section.hidden)
                if owner_user_id is not None:
                    cache_manager.delete(UserCacheKey.PROFILE_SECTIONS.format(user_id=owner_user_id))
                return True
        except (django.db.DatabaseError,ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def get_user_profile_sections(self,user,includehidden=False):
        """
        :param user:
        :param includehidden:
        :return:
        """
        if user is None:
            return []
        cache_key = UserCacheKey.PROFILE_SECTIONS.format(user_id=user.id)
        all_sections = cache_manager.get(cache_key)
        if all_sections is None:
            all_sections = list(self.filter(user_id=user.id))
            cache_manager.set(cache_key, all_sections, timeout=3600)
        return all_sections if includehidden else [s for s in all_sections if not s.hidden]
    def create_default_user_sections(self, user_id):
        """
        Creates the default user sections after the account gets created
        :param user_id:
        :return:None
        """
        from django.conf import settings
        for key, value in settings.DEFAULT_SECTIONS.items():
            UserProfileSection.objects.get_or_create(
                user_id=user_id,
                name=key,
                defaults={'content': value, 'hidden': False}
            )

class UserProfileSection(models.Model):
    user = models.ForeignKey(User,on_delete=models.CASCADE)
    name = models.CharField(max_length=100,blank=False)
    content = models.CharField(max_length=500,blank=False)
    objects = CustomUserProfileSectionManager()
    hidden = models.BooleanField(default=False)
    class Meta:
        db_table = 'profile_sections'

class UserTechnicalSkillsManager(models.Manager):
    def add_user_skill(self,name,section_id,user):
        """
        Adds `name` to `section_id`, but only if that section belongs to `user`
        - otherwise anyone could add skills to another user's tech-stack just
          by guessing/knowing their section_id.
        :param name:
        :param section_id:
        :param user: the requesting user; section_id must belong to them
        :return:
        """
        if not UserTechnicalSkillSection.objects.filter(id=section_id, user=user).exists():
            return None
        try:
            with transaction.atomic():
                already_existing = self.filter(name=name,section_id=section_id).select_for_update().first()
                if already_existing:
                    transaction.set_rollback(True)
                    return None
                result = self.get_or_create(name=name,section_id=section_id) if not already_existing else None
            if result is not None:
                cache_manager.delete(UserCacheKey.TECHSTACK.format(user_id=user.id))
            return result
        except (django.db.DatabaseError, ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def remove_user_skill(self,skill,user):
        """
        Deletes `skill` only if it belongs to a section owned by `user` - the
        section__user join is baked directly into the delete's filter so the
        ownership check and the delete happen as one atomic query, instead of
        a separate check-then-delete (which would be racy and, before this fix,
        was missing entirely - any authenticated user could delete any skill
        just by knowing its id).
        :param skill:
        :param user: the requesting user; only their own skills may be deleted
        :return: True if a row was actually deleted, False if the skill doesn't
                 exist or isn't owned by `user`
        """
        if not skill:
            raise django.db.DatabaseError("Skill cannot be None")
        owner_user_id = UserTechnicalSkillSection.objects.filter(id=skill.section_id).values_list('user_id', flat=True).first()
        deleted_count, _ = self.filter(id=skill.id, section__user=user).delete()
        if deleted_count and owner_user_id is not None:
            cache_manager.delete(UserCacheKey.TECHSTACK.format(user_id=owner_user_id))
        return deleted_count > 0
    def get_skills_from_section(self, section_id):
        """

        :param section_id:
        :return:
        """
        return self.filter(section_id=section_id)

class UserTechnicalSkillSectionManager(models.Manager):
    def create_user_default_techstack(self,user_id):
        """
        Creates the default tech stack categories for any user profile after creating account
        :param user_id:
        :return:
        """
        from django.conf import settings
        user = User.objects.get(id=user_id)
        for name in settings.DEFAULT_TECHSTACK_CATEGORIES:
            UserTechnicalSkillSection.objects.get_or_create(
                user=user,
                name=name
            )

    def get_user_techstack(self,user):
        """
        Returns an user's whole techstack
        :param user:
        :return:A dictionary with elements of type "tech-stack category":"User skills from that one category"
        """
        cache_key = UserCacheKey.TECHSTACK.format(user_id=user.id)
        tech_dict = cache_manager.get(cache_key)
        if tech_dict is not None:
            return tech_dict
        sections = self.filter(user=user)
        tech_dict = {}
        for section in sections:
            tech_dict[section] = []
            for skill in UserTechnicalSkill.objects.get_skills_from_section(section.id):
                tech_dict[section].append(skill)
        cache_manager.set(cache_key, tech_dict, timeout=3600)
        return tech_dict

class UserTechnicalSkillSection(models.Model):
    name = models.CharField(max_length=100,blank=False)
    user = models.ForeignKey(User,on_delete=models.CASCADE)
    objects = UserTechnicalSkillSectionManager()
    class Meta:
        db_table = 'technical_skill_sections'

class UserTechnicalSkill(models.Model):
    name = models.CharField(max_length=100,blank=False)
    section = models.ForeignKey(UserTechnicalSkillSection,on_delete=models.CASCADE)
    objects = UserTechnicalSkillsManager()
    class Meta:
        db_table = 'technical_skills'

class UserExperienceSubsection(models.Model):
    name = models.CharField(max_length=100,default='Add your experience working on this project',blank=True)
    description = models.CharField(max_length=500)
    user_section = models.ForeignKey(UserProfileSection,on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()

class RequestManager(models.Manager):
    def find_request(self,sender,receiver):
        try:
            return self.filter(Q(sender=sender,receiver=receiver)|Q(sender=receiver,receiver=sender))
        except django.db.DatabaseError as e:
            print(str(e))
            return None

    def send_friend_request(self, sender, receiver):
        """
        :param sender:
        :param receiver:
        :return:
        """
        try:
            with transaction.atomic():
                found = self.find_request(sender, receiver)
                if found:
                    transaction.set_rollback(True)
                    return None
            obj, created = self.get_or_create(
                sender=sender,
                receiver=receiver,
                request_type='friend',
                status= 'pending',
                timestamp= timezone.now()
            )
            cache_manager.delete(UserCacheKey.FRIENDSHIP_REQUESTS.format(user_id=receiver.id))
            return obj
        except Exception as err:
            print(f"Eroare ORM: {str(err)}")

            obj, created = self.get_or_create(
                    sender=sender,
                    receiver=receiver,
                    request_type='friend',
                    status='pending',
                    timestamp=timezone.now()
                )
            cache_manager.delete(UserCacheKey.FRIENDSHIP_REQUESTS.format(user_id=receiver.id))
            return obj
        except (django.db.DatabaseError, ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def accept_request(self,request):
        try:
            with transaction.atomic():
                found = self.select_for_update().filter(
                    sender=request.sender,
                    receiver=request.receiver
                ).first()
                if found is None:
                    raise django.db.DatabaseError("Request wasn't found")
                if found.status != 'pending':
                    raise ValueError("Request was already handled")
                found.status = 'accepted'
                Friendship.objects.create(sender=request.sender,receiver=request.receiver)
                found.save()
            cache_manager.delete(UserCacheKey.FRIENDSHIP_REQUESTS.format(user_id=request.receiver_id))
            return found
        except (django.db.DatabaseError,ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def deny_request(self,request):
        try:
            with transaction.atomic():
                found = self.select_for_update().filter(
                    sender=request.sender,
                    receiver=request.receiver
                ).first()
                if found is None:
                    raise django.db.DatabaseError("Request wasn't found")
                if found.status != 'pending':
                    raise ValueError("Request was already handled")
                found.status = 'declined'
                found.save()
            cache_manager.delete(UserCacheKey.FRIENDSHIP_REQUESTS.format(user_id=request.receiver_id))
            return found
        except (django.db.DatabaseError, ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def get_user_requests(self,user):
        try:
            return self.filter(receiver=user)
        except django.db.DatabaseError:
            return []

    def get_pending_friend_requests(self,user):
        cache_key = UserCacheKey.FRIENDSHIP_REQUESTS.format(user_id=user.id)
        requests = cache_manager.get(cache_key)
        if requests is None:
            requests = list(self.filter(receiver=user, request_type='friend', status='pending'))
            cache_manager.set(cache_key, requests, timeout=3600)
        return requests

    def remove_request(self, request):
        try:
            receiver_id, request_type = request.receiver_id, request.request_type
            deleted_count, _ = request.delete()
            if request_type == 'friend':
                cache_manager.delete(UserCacheKey.FRIENDSHIP_REQUESTS.format(user_id=receiver_id))
            return deleted_count > 0
        except django.db.DatabaseError:
            return False

    def send_project_join_request(self,sender,receivers):
        try:
            with transaction.atomic():
                already_sent = self.filter(sender=sender,receiver__in=receivers,status='pending')
                if already_sent.exists():
                    transaction.set_rollback(True)
                    return None
                return [
                    self.create(sender=sender,
                            receiver=receiver,
                            request_type='project',
                            status='pending')
                    for receiver in receivers
                ]
        except django.db.DatabaseError as e:
            print(str(e))
            return None

    def handle_move_file_access_request(self,sender,receiver,response):
        from projects.models import Project, ResourceAccess
        try:
            with transaction.atomic():
                found = self.select_for_update().filter(
                    sender=sender,
                    receiver=receiver,
                    request_type='move_file_access',
                    status='pending'
                ).first()
                if found is None:
                    return False
                if response == 'ACCEPT':
                    match = re.match(r'\[.*?\]Requesting acces for URL:(.*) in project (.*)$', found.target or '')
                    if not match:
                        transaction.set_rollback(True)
                        return False
                    file_url, project_name = match.group(1), match.group(2)
                    project = Project.objects.filter(name=project_name).first()
                    if project is None:
                        transaction.set_rollback(True)
                        return False
                    if not ResourceAccess.objects.lock_file(file_url, project, found.sender):
                        transaction.set_rollback(True)
                        return False
                found.status = 'accepted' if response == 'ACCEPT' else 'declined'
                found.save(update_fields=['status'])
                return True
        except django.db.DatabaseError as e:
            print(str(e))
            return False

    def send_project_invitation(self,sender,receiver):
        print('te rog')#e mecanic...il fac alta data...

    def send_files_access_request(self,user,project,requested_access,valid_admins):
        try:
            with transaction.atomic():
                return self.bulk_create(
                    [UserRequest(sender_id=user.id,
                                 request_type='file_access',
                                 status='pending',
                                 receiver_id=admin.id,
                                 target='Requesting acces for files {} in project {}'.format(requested_access,project.name),
                                 ) for admin in valid_admins]
                )
        except django.db.DatabaseError as e:
            print(str(e))

    def send_file_move_access_request(self,file,sender,receiver,project):
        try:
            with transaction.atomic():
                return self.create(
                    sender_id=sender.id,
                    receiver_id=receiver.id,
                    status='pending',
                    request_type='move_file_access',
                    target='[{}]Requesting acces for URL:{} in project {}'.format(sender.username,file,project.name)
                ) is not None
        except django.db.DatabaseError as e:
            print(str(e))
            return False

class UserRequest(models.Model):
    id = models.BigAutoField(primary_key=True)
    sender = models.ForeignKey(
            User,
            on_delete=models.CASCADE,
            related_name='request_sender'
    )
    receiver = models.ForeignKey(
            User,
            on_delete=models.CASCADE,
            related_name='request_receiver'
    )
    timestamp = models.DateTimeField(default=timezone.now,db_index=True)
    request_type = models.CharField(
        max_length=20,
        choices=[('friend', 'friend'), ('project', 'project'), ('file_access', 'file_access'), ('move_file_access', 'move_file_access')]
    )
    target = models.CharField(max_length=255, null=True, blank=True, db_index=True,default=None)
    status = models.CharField(
        max_length=20,
        choices=[('pending', 'pending'), ('declined', 'declined'), ('accepted', 'accepted')]
    )
    objects = RequestManager()
    class Meta:
        db_table = 'requests'
        constraints = [
            models.CheckConstraint(
                condition=Q(request_type__in=['friend','project','file_access','move_file_access']),
                name='check_valid_request_type',
            ),
            models.CheckConstraint(
                condition=Q(status__in=['pending', 'declined', 'accepted']),
                name='check_valid_status',
            )
        ]
class FriendshipManager(models.Manager):
    def remove_friendship(self, friend1, friend2):
        try:
            fr = self.get(Q(sender=friend1, receiver=friend2) | Q(sender=friend2, receiver=friend1))
        except Friendship.DoesNotExist:
            return False
        deleted_count, _ = fr.delete()
        return deleted_count > 0
    def find_friendship(self,friend1,friend2):
        try:
            fr = self.filter(Q(sender=friend1,receiver=friend2)|Q(sender=friend2,receiver=friend1))
            return fr
        except django.db.DatabaseError:
            return None
class Friendship(models.Model):
    sender = models.ForeignKey(User,on_delete=models.CASCADE,related_name='friend1')
    receiver = models.ForeignKey(User,on_delete=models.CASCADE,related_name='friend2')
    startdate = models.DateTimeField(default=timezone.now)
    objects = FriendshipManager()
    class Meta:
        db_table = 'friendships'