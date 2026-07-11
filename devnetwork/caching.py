from abc import ABC, abstractmethod
from enum import Enum

from django.core.cache import cache as django_cache


class CacheManager(ABC):
    """
    Backend-agnostic cache interface. django.core.cache.cache already works
    against Redis/Memcached/LocMem interchangeably for get/set/delete, so
    those are thin passthroughs here. delete_pattern is the one operation
    that genuinely differs per backend (wildcard key scanning is a Redis-only
    concept), which is why it's the one method every backend must implement
    for itself instead of sharing a single implementation.
    """
    @abstractmethod
    def get(self, key, default=None):
        ...
    @abstractmethod
    def set(self, key, value, timeout=None):
        ...
    @abstractmethod
    def delete(self, key):
        ...
    @abstractmethod
    def delete_many(self, keys):
        ...
    @abstractmethod
    def delete_pattern(self, pattern):
        """Deletes every key matching a glob-style pattern (e.g. 'github_file_owner_repo_*')."""
        ...


class RedisCacheManager(CacheManager):
    """Current backend: django-redis on top of Django's cache framework."""
    def get(self, key, default=None):
        return django_cache.get(key, default)
    def set(self, key, value, timeout=None):
        django_cache.set(key, value, timeout=timeout)
    def delete(self, key):
        django_cache.delete(key)
    def delete_many(self, keys):
        if keys:
            django_cache.delete_many(keys)
    def delete_pattern(self, pattern):
        matched_keys = list(django_cache.keys(pattern))
        if matched_keys:
            django_cache.delete_many(matched_keys)


cache_manager: CacheManager = RedisCacheManager()

class UserCacheKey(str, Enum):
    PROFILE_DATA = 'users:profile_data:{user_id}'
    PROFILE_SECTIONS = 'users:profile_sections:{user_id}'
    TECHSTACK = 'users:techstack:{user_id}'
    PROJECTS = 'users:projects:{user_id}'
    FRIENDSHIP_REQUESTS = 'users:friendship_requests:{user_id}'
    # Reserved for the future React-based inbox/notifications feature.
    # No manager method reads or writes this key yet.
    NOTIFICATIONS = 'users:notifications:{user_id}'