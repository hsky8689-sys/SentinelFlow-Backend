import django.db
from django.db.models import Q
from django.db import models,transaction
from django.contrib.auth.models import AbstractBaseUser,BaseUserManager,PermissionsMixin
from datetime import datetime
from django.utils import timezone
from django.db import transaction

class CustomUserManager(BaseUserManager):
    def create_user(self,username,email,password,birthday):
        with transaction.atomic():
            user = self.model(username=username, email=email, birthday=birthday)
            user.set_password(password)
            user.save(using=self._db)
            UserProfileSection.objects.create_default_user_sections(user.id)
            UserTechnicalSkillSection.objects.create_user_default_techstack(user.id)
            return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, email, password, **extra_fields)
    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD:username})

class User(AbstractBaseUser,PermissionsMixin):
    username = models.CharField(max_length=100, blank=False, unique=True)
    login_date = models.DateTimeField(default=datetime.now)
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

class UserProfileData(models.Model):
    user = models.ForeignKey(User,on_delete=models.CASCADE)
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
    def delete_user_profile_section(self,user:User,section_id):
        """
        Deletes a former profile section from an user's personal page
        :param user:
        :return:true or false if the section was updated accordingly
        """
        self.filter(id=section_id,user_id=user.id).delete()
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
                former_section.update(name=new_section.name,content=new_section.content,hidden=new_section.hidden)
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
        return self.filter(user_id=user.id) if includehidden else self.filter(user_id=user.id,hidden=False)
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
    def add_user_skill(self,name,section_id):
        """

        :param name:
        :param section_id:
        :return:
        """
        try:
            with transaction.atomic():
                already_existing = self.filter(name=name,section_id=section_id).select_for_update().first()
                if already_existing:
                    transaction.set_rollback(True)
                    return None
                return self.get_or_create(name=name,section_id=section_id) if not already_existing else None
        except (django.db.DatabaseError, ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def remove_user_skill(self,skill):
        """

        :param skill:
        :return:
        """
        if not skill:
            raise django.db.DatabaseError("Skill cannot be None")
        return self.get(id=skill.id).delete()
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
        sections = self.filter(user=user)
        tech_dict = {}
        for section in sections:
            tech_dict[section] = []
            for skill in UserTechnicalSkill.objects.get_skills_from_section(section.id):
                tech_dict[section].append(skill)
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

class PostManager(models.Manager):
    def find_user_posts(self,user_id):
        return self.filter(user_id=user_id)
class Post(models.Model):
    description = models.CharField(max_length=500)
    user = models.ForeignKey(User,on_delete=models.CASCADE)

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
                    status='pending',
                    timestamp=timezone.now()
                )
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
                return found
        except (django.db.DatabaseError, ValueError) as err:
            print(f"Error handling request: {str(err)}")
            return None

    def get_user_requests(self,user):
        try:
            return self.filter(receiver=user)
        except django.db.DatabaseError:
            return []

    def remove_request(self, request):
        try:
            deleted_count, _ = request.delete()
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

    def send_project_invitation(self,sender,receiver):
        print('te rog')#e mecanic...il fac alta data...

    def send_files_access_request(self,user,project,requested_access,valid_admins):
        try:
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

class UserRequest(models.Model):
    pk = models.CompositePrimaryKey("sender_id","receiver_id")
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
    timestamp = models.DateTimeField(default=datetime.now,db_index=True)
    request_type = models.CharField(
        max_length=20,
        choices=[('friend', 'friend'), ('project', 'project'), ('file_access', 'file_access')]
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
                condition=Q(request_type__in=['friend','project','file_access']),
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
    startdate = models.DateTimeField(default=datetime.now)
    objects = FriendshipManager()
    class Meta:
        db_table = 'friendships'