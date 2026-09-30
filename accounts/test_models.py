from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from accounts.models import UserProfile


class UserProfileModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='model_test_user',
            password='secretpassword123',
            first_name='John',
            last_name='Doe'
        )

    def test_user_profile_string_representation(self):
        """Verify that UserProfile.__str__ returns '{username}\'s Profile'."""
        profile = self.user.profile
        self.assertEqual(str(profile), "model_test_user's Profile")

    def test_user_profile_auto_created_on_user_creation(self):
        """Verify that User post_save signal automatically instantiates UserProfile."""
        new_user = User.objects.create_user(username='new_operator', password='pw')
        self.assertTrue(UserProfile.objects.filter(user=new_user).exists())
        self.assertEqual(new_user.profile.user, new_user)
        self.assertIsNone(new_user.profile.last_seen)

    def test_user_profile_safeguard_on_existing_user_save(self):
        """Verify that saving an existing user executes get_or_create without duplicating profile."""
        profile_count_before = UserProfile.objects.count()
        self.user.first_name = 'Jonathan'
        self.user.save()
        profile_count_after = UserProfile.objects.count()
        self.assertEqual(profile_count_before, profile_count_after)
        self.assertEqual(self.user.profile.user.first_name, 'Jonathan')

    def test_user_profile_cascade_delete(self):
        """Verify that deleting a User cascades and deletes the associated UserProfile."""
        user_id = self.user.id
        self.assertTrue(UserProfile.objects.filter(user_id=user_id).exists())
        self.user.delete()
        self.assertFalse(UserProfile.objects.filter(user_id=user_id).exists())

    def test_user_profile_last_seen_persistence(self):
        """Verify updating and persisting timezone-aware last_seen timestamps."""
        now = timezone.now()
        profile = self.user.profile
        profile.last_seen = now
        profile.save()

        profile.refresh_from_db()
        self.assertIsNotNone(profile.last_seen)
        self.assertEqual(profile.last_seen, now)
