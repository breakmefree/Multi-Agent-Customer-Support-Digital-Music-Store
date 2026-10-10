"""Preference memory tests."""

from src.memory.preferences import (
	UserProfile,
	load_preferences,
	merge_and_save_preferences,
)


def test_user_profile_defaults_to_no_preferences():
	profile = UserProfile(customer_id=42)

	assert profile.customer_id == 42
	assert profile.music_preferences == []


def test_preferences_merge_without_duplicates_and_are_isolated():
	merge_and_save_preferences(42001, {"music_preferences": ["jazz"]})
	merge_and_save_preferences(42001, {"music_preferences": ["jazz", "blues"]})

	assert load_preferences(42001) == {
		"music_preferences": ["jazz", "blues"],
	}
	assert load_preferences(42002) == {}
