from datetime import datetime
from loguru import logger
from src.utils.client import create_twitter_client
from curl_cffi.requests import AsyncSession

from src.utils.config import Config
from src.utils.decorators import retry_async


class Constants:
    LIKE_QUERY_ID = "lI07N6Otwv1PhnEgXILM7A"
    RETWEET_QUERY_ID = "ojPdsZsimiJrUGLR1sjUtA"
    TWEET_QUERY_ID = "IVdJU2Vjw2llhmJOAZy9Ow"
    BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

    def __init__(self):
        self.LIKE_QUERY_ID = "lI07N6Otwv1PhnEgXILM7A"
        self.RETWEET_QUERY_ID = "ojPdsZsimiJrUGLR1sjUtA"
        self.TWEET_QUERY_ID = "IVdJU2Vjw2llhmJOAZy9Ow"
        self.BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"


class Twitter:
    def __init__(self, account_index: int, auth_token: str, proxy: str, config: Config):
        self.account_index = account_index
        self.auth_token = auth_token
        self.proxy = proxy
        self.config = config

        self.account_status: str = "ok"
        self.session: AsyncSession | None = None
        self.csrf_token: str | None = None
        self.username: str | None = None

    @retry_async(default_value=False)
    async def initialize(self):
        try:
            self.session, self.csrf_token = await create_twitter_client(
                self.proxy, self.auth_token, self.config.OTHERS.SKIP_SSL_VERIFICATION
            )

            # Try to get username - this is where most auth failures occur
            try:
                self.username = await self.get_account_username()
                if not self.username:
                    self.account_status = "wrong_token"
                    logger.error(
                        f"[{self.account_index}] Failed to get username - invalid auth token"
                    )
                    return False
            except Exception as username_error:
                # Handle specific error cases from get_account_username
                if "Could not authenticate you" in str(username_error):
                    self.account_status = "wrong_token"
                    logger.error(
                        f"[{self.account_index}] Authentication failed: Invalid auth token"
                    )
                elif "account is temporarily locked" in str(username_error):
                    self.account_status = "locked"
                    logger.error(
                        f"[{self.account_index}] Account is temporarily locked"
                    )
                elif "to protect our users from spam" in str(username_error):
                    self.account_status = "suspended"
                    logger.error(f"[{self.account_index}] Account is suspended")
                else:
                    self.account_status = "unknown"
                    logger.error(
                        f"[{self.account_index}] Failed to get username: {username_error}"
                    )
                return False

            logger.success(
                f"[{self.account_index}] Successfully initialized account: {self.username}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to initialize: {e}")
            # Update account status even on general errors
            if "Could not authenticate you" in str(e):
                self.account_status = "wrong_token"
            elif "account is temporarily locked" in str(e):
                self.account_status = "locked"
            elif "to protect our users from spam" in str(e):
                self.account_status = "suspended"
            else:
                self.account_status = "unknown"
            return False

    @retry_async(default_value=False)
    async def like(self, tweet_id: str):
        try:
            headers = {
                "x-csrf-token": self.csrf_token,
            }

            json_data = {
                "variables": {
                    "tweet_id": tweet_id,
                },
                "queryId": Constants.LIKE_QUERY_ID,
            }

            response = await self.session.post(
                f"https://x.com/i/api/graphql/{Constants.LIKE_QUERY_ID}/FavoriteTweet",
                headers=headers,
                json=json_data,
            )

            await self._update_cookies()

            if response.status_code != 200:
                should_continue = await self._verify_error_response(response.text)
                if not should_continue:
                    return False

            if (
                '"Done"' in response.text
                or "was already liked" in response.text
                or "has already favorited tweet" in response.text
            ):
                logger.success(
                    f"[{self.account_index}] Successfully liked tweet: {tweet_id}"
                )
                return True
            else:
                raise Exception(f"{response.text}")

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to like tweet: {e}")
            raise e

    @retry_async(attempts=1, default_value=False)
    async def follow(self, username: str):
        try:
            user_info = await self._get_user_info_by_username(username)
            if not user_info:
                raise Exception(f"failed to get user info")

            headers = {
                "content-type": "application/x-www-form-urlencoded",
                "referer": f"https://x.com/{username}",
                "x-csrf-token": self.csrf_token,
            }

            data = {
                "include_profile_interstitial_type": "1",
                "include_blocking": "1",
                "include_blocked_by": "1",
                "include_followed_by": "1",
                "include_want_retweets": "1",
                "include_mute_edge": "1",
                "include_can_dm": "1",
                "include_can_media_tag": "1",
                "include_ext_is_blue_verified": "1",
                "include_ext_verified_type": "1",
                "include_ext_profile_image_shape": "1",
                "skip_status": "1",
                "user_id": user_info["user_id"],
            }

            response = await self.session.post(
                f"https://x.com/i/api/1.1/friendships/create.json",
                headers=headers,
                data=data,
            )

            await self._update_cookies()

            if response.status_code != 200:
                should_continue = await self._verify_error_response(response.text)
                if not should_continue:
                    return False

            if response.json()["screen_name"].lower() == username.lower():
                logger.success(
                    f"[{self.account_index}] Successfully followed user: {username}"
                )
                return True
            else:
                raise Exception(f"{response.text}")

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to follow user: {e}")
            raise e

    @retry_async(attempts=1, default_value=False)
    async def retweet(self, tweet_id: str):
        try:
            headers = {
                "x-csrf-token": self.csrf_token,
            }

            json_data = {
                "variables": {
                    "tweet_id": tweet_id,
                    "dark_request": False,
                },
                "queryId": Constants.RETWEET_QUERY_ID,
            }

            response = await self.session.post(
                f"https://x.com/i/api/graphql/{Constants.RETWEET_QUERY_ID}/CreateRetweet",
                headers=headers,
                json=json_data,
            )
            
            if response.status_code > 399:
                logger.error(f"[{self.account_index}] Status: {response.status_code} | Text: {response.text}")
                return False
            
            await self._update_cookies()

            if response.status_code != 200:
                should_continue = await self._verify_error_response(response.text)
                if not should_continue:
                    return False

            rest_id = (
                response.json()
                .get("data", {})
                .get("create_retweet", {})
                .get("retweet_results", {})
                .get("result", {})
                .get("rest_id", None)
            )

            if rest_id or "You have already retweeted this Tweet" in response.text:
                logger.success(
                    f"[{self.account_index}] Successfully retweeted tweet: {tweet_id}"
                )
                return True
            else:
                raise Exception(f"{response.text}")

        except Exception as e:
            if "This request looks like it might be automated" in response.text:
                raise Exception(
                    "Can't complete this action right now. Please try again later."
                )

            logger.error(f"[{self.account_index}] Failed to retweet tweet: {e}")
            raise e

    @retry_async(attempts=1, default_value=False)
    async def tweet(
        self, text: str, quote_tweet_url: str = None, media_base64: str = None
    ):
        """
        Tweet a message with optional media attachment.

        Args:
            text (str): The text content of the tweet.
            quote_tweet_url (str, optional): URL of the quote tweet to attach.
            media_base64 (str, optional): Base64-encoded media to attach.

        Returns:
            bool: True if the tweet was successful, False otherwise.
        """
        try:
            media_id = None
            if media_base64:
                media_id = await self._upload_media(media_base64)
                if not media_id:
                    raise Exception("Failed to upload media")

            # Build URL and request body
            base_url = f"https://twitter.com/i/api/graphql/{Constants.TWEET_QUERY_ID}/CreateTweet"

            # Build variables based on options
            variables = {
                "tweet_text": text,
                "dark_request": False,
                "semantic_annotation_ids": [],
            }

            # Add media if provided
            if media_id:
                variables["media"] = {
                    "media_entities": [
                        {
                            "media_id": media_id,
                            "tagged_users": [],
                        }
                    ],
                    "possibly_sensitive": False,
                }
            else:
                variables["media"] = {
                    "media_entities": [],
                    "possibly_sensitive": False,
                }

            # Add quote tweet URL if provided
            if quote_tweet_url:
                variables["attachment_url"] = quote_tweet_url

            # Build the full request body
            request_body = {
                "variables": variables,
                "features": {
                    "premium_content_api_read_enabled": False,
                    "communities_web_enable_tweet_community_results_fetch": True,
                    "c9s_tweet_anatomy_moderator_badge_enabled": True,
                    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
                    "responsive_web_grok_analyze_post_followups_enabled": True,
                    "responsive_web_jetfuel_frame": False,
                    "responsive_web_grok_share_attachment_enabled": True,
                    "responsive_web_edit_tweet_api_enabled": True,
                    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                    "view_counts_everywhere_api_enabled": True,
                    "longform_notetweets_consumption_enabled": True,
                    "responsive_web_twitter_article_tweet_consumption_enabled": True,
                    "tweet_awards_web_tipping_enabled": False,
                    "responsive_web_grok_show_grok_translated_post": False,
                    "responsive_web_grok_analysis_button_from_backend": True,
                    "creator_subscriptions_quote_tweet_preview_enabled": False,
                    "longform_notetweets_rich_text_read_enabled": True,
                    "longform_notetweets_inline_media_enabled": True,
                    "profile_label_improvements_pcf_label_in_post_enabled": True,
                    "rweb_tipjar_consumption_enabled": True,
                    "responsive_web_graphql_exclude_directive_enabled": True,
                    "verified_phone_label_enabled": False,
                    "articles_preview_enabled": True,
                    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                    "freedom_of_speech_not_reach_fetch_enabled": True,
                    "standardized_nudges_misinfo": True,
                    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                    "responsive_web_grok_image_annotation_enabled": True,
                    "responsive_web_graphql_timeline_navigation_enabled": True,
                    "responsive_web_enhance_cards_enabled": False,
                },
                "queryId": Constants.TWEET_QUERY_ID,
            }

            headers = {
                "origin": "https://twitter.com",
                "referer": "https://twitter.com/compose/tweet",
                "x-csrf-token": self.csrf_token,
            }

            response = await self.session.post(
                base_url,
                headers=headers,
                json=request_body,
            )
            await self._update_cookies()

            if response.status_code != 200:
                should_continue = await self._verify_error_response(response.text)
                if not should_continue:
                    return False

            response_json = response.json()

            tweet_id = (
                response_json.get("data", {})
                .get("create_tweet", {})
                .get("tweet_results", {})
                .get("result", {})
                .get("rest_id", None)
            )

            if tweet_id:
                logger.success(
                    f"[{self.account_index}] Successfully created tweet with ID: {tweet_id}"
                )
                return True
            elif "You have reached your daily limit" in response.text:
                logger.error(
                    f"[{self.account_index}] Daily limit reached. Can't tweet."
                )
                return False
            elif "This request looks like it might be automated" in response.text:
                logger.error(
                    f"[{self.account_index}] Can't tweet. Try to change the proxy or try again later."
                )
                return False
            elif "Status is a duplicate" in response.text:
                logger.success(f"[{self.account_index}] Tweet already exists.")
                return True
            else:
                raise Exception(f"Failed to create tweet: {response.text}")

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to create tweet: {e}")
            raise e

    @retry_async(attempts=1, default_value=False)
    async def comment(self, text: str, tweet_id: str, media_base64: str = None):
        """
        Comment on a tweet with optional media attachment.

        Args:
            text (str): The text content of the comment.
            tweet_id (str): The ID of the tweet to comment on.
            media_base64 (str, optional): Base64-encoded media to attach.

        Returns:
            bool: True if the comment was successful, False otherwise.
        """
        try:
            media_id = None
            if media_base64:
                media_id = await self._upload_media(media_base64)
                if not media_id:
                    raise Exception("Failed to upload media")

            # Build URL and request body
            base_url = (
                f"https://x.com/i/api/graphql/{Constants.TWEET_QUERY_ID}/CreateTweet"
            )

            # Build variables based on options
            variables = {
                "tweet_text": text,
                "reply": {
                    "in_reply_to_tweet_id": tweet_id,
                    "exclude_reply_user_ids": [],
                },
                # "batch_compose":"BatchSubsequent",
                "dark_request": False,
                "semantic_annotation_ids": [],
                "disallowed_reply_options": None,
            }

            # Add media if provided
            if media_id:
                variables["media"] = {
                    "media_entities": [
                        {
                            "media_id": media_id,
                            "tagged_users": [],
                        }
                    ],
                    "possibly_sensitive": False,
                }
            else:
                variables["media"] = {
                    "media_entities": [],
                    "possibly_sensitive": False,
                }

            # Build the full request body
            request_body = {
                "variables": variables,
                "features": {
                    "premium_content_api_read_enabled": False,
                    "communities_web_enable_tweet_community_results_fetch": True,
                    "c9s_tweet_anatomy_moderator_badge_enabled": True,
                    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
                    "responsive_web_grok_analyze_post_followups_enabled": True,
                    "responsive_web_jetfuel_frame": False,
                    "responsive_web_grok_share_attachment_enabled": True,
                    "responsive_web_edit_tweet_api_enabled": True,
                    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                    "view_counts_everywhere_api_enabled": True,
                    "longform_notetweets_consumption_enabled": True,
                    "responsive_web_twitter_article_tweet_consumption_enabled": True,
                    "tweet_awards_web_tipping_enabled": False,
                    "responsive_web_grok_show_grok_translated_post": False,
                    "responsive_web_grok_analysis_button_from_backend": True,
                    "creator_subscriptions_quote_tweet_preview_enabled": False,
                    "longform_notetweets_rich_text_read_enabled": True,
                    "longform_notetweets_inline_media_enabled": True,
                    "profile_label_improvements_pcf_label_in_post_enabled": True,
                    "rweb_tipjar_consumption_enabled": True,
                    "responsive_web_graphql_exclude_directive_enabled": True,
                    "verified_phone_label_enabled": False,
                    "articles_preview_enabled": True,
                    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                    "freedom_of_speech_not_reach_fetch_enabled": True,
                    "standardized_nudges_misinfo": True,
                    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                    "responsive_web_grok_image_annotation_enabled": True,
                    "responsive_web_graphql_timeline_navigation_enabled": True,
                    "responsive_web_enhance_cards_enabled": False,
                },
                "queryId": Constants.TWEET_QUERY_ID,
            }

            headers = {
                "origin": "https://x.com",
                "referer": "https://x.com/compose/post",
                "x-csrf-token": self.csrf_token,
            }

            response = await self.session.post(
                base_url,
                headers=headers,
                json=request_body,
            )
            await self._update_cookies()

            if response.status_code != 200:
                should_continue = await self._verify_error_response(response.text)
                if not should_continue:
                    return False

            response_json = response.json()

            tweet_id = (
                response_json.get("data", {})
                .get("create_tweet", {})
                .get("tweet_results", {})
                .get("result", {})
                .get("rest_id", None)
            )

            if tweet_id:
                logger.success(
                    f"[{self.account_index}] Successfully created comment with ID: {tweet_id}"
                )
                return True
            elif "You have reached your daily limit" in response.text:
                logger.error(
                    f"[{self.account_index}] Daily limit reached. Can't comment."
                )
                return False
            elif "This request looks like it might be automated" in response.text:
                raise Exception(
                    f"Can't comment now. Try to change the proxy or try again later."
                )

            elif "Status is a duplicate" in response.text:
                logger.success(f"[{self.account_index}] Comment already exists.")
                return True

            elif "Tweet that is deleted or not visible to you" in response.text:
                logger.success(
                    f"[{self.account_index}] Tweet is deleted or not visible to you."
                )
                return True

            else:
                raise Exception(f"Failed to create comment: {response.text}")

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to create comment: {e}")
            raise e

    @retry_async(default_value=None)
    async def _get_user_info_by_username(self, username: str):
        """
        Get user info by username.
        return user_info: dict
        user_info = {
            'user_id': str,
            'name': str,
            'username': str,
        }
        """
        try:
            headers = {
                "content-type": "application/json",
                "referer": f"https://x.com/{username}",
                "x-csrf-token": self.csrf_token,
            }
            params = {
                "variables": f'{{"screen_name":"{username}"}}',
                "features": '{"hidden_profile_subscriptions_enabled":true,"profile_label_improvements_pcf_label_in_post_enabled":true,"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"subscriptions_verification_info_is_identity_verified_enabled":true,"subscriptions_verification_info_verified_since_enabled":true,"highlights_tweets_tab_ui_enabled":true,"responsive_web_twitter_article_notes_tab_enabled":true,"subscriptions_feature_can_gift_premium":true,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline_navigation_enabled":true}',
                "fieldToggles": '{"withAuxiliaryUserLabels":true}',
            }

            response = await self.session.get(
                "https://x.com/i/api/graphql/32pL5BWe9WKeSK1MoPvFQQ/UserByScreenName",
                params=params,
                headers=headers,
            )
            await self._update_cookies()

            data_item = response.json().get("data", {})
            if not data_item:
                raise Exception(f"{response.text}")

            data = response.json()["data"]["user"]["result"]
            user_info = {}

            user_info["user_id"] = data.get("rest_id", None)
            user_info["name"] = data.get("legacy", {}).get("name", None)
            user_info["username"] = username

            return user_info

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to get user info: {e}")
            raise e


    @retry_async(default_value=None)
    async def get_account_username(self):
        try:
            # Build URL with query parameters
            base_url = "https://api.x.com/graphql/UhddhjWCl-JMqeiG4vPtvw/Viewer"

            params = {
                "variables": '{"withCommunitiesMemberships":true}',
                "features": '{"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_phone_label_enabled":false,"creator_subscriptions_tweet_preview_api_enabled":true,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline_navigation_enabled":true}',
                "fieldToggles": '{"isDelegate":false,"withAuxiliaryUserLabels":false}',
            }

            headers = {
                "x-csrf-token": self.csrf_token,
                "x-twitter-active-user": "no",
            }

            response = await self.session.get(
                base_url,
                params=params,
                headers=headers,
            )
            if 'Rate limit exceeded' in response.text:
                logger.error(f"[{self.account_index}] Rate limit exceeded")
                return None
            
            await self._update_cookies()

            if response.status_code != 200:
                should_continue = await self._verify_error_response(response.text)
                if not should_continue:
                    return None

            response_json = response.json()

            # Extract username from the response
            username = (
                response_json.get("data", {})
                .get("viewer", {})
                .get("user_results", {})
                .get("result", {})
                .get("legacy", {})
                .get("screen_name", None)
            )

            created_at = (
                response_json.get("data", {})
                .get("viewer", {})
                .get("user_results", {})
                .get("result", {})
                .get("legacy", {})
                .get("created_at", None)
            )

            # Format the created_at date if it exists
            formatted_created_at = None
            if created_at:
                try:
                    # Parse Twitter's date format (e.g., "Tue Jan 21 20:58:28 +0000 2025")
                    date_obj = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                    # Format to "DD/MM/YYYY HH:MM"
                    formatted_created_at = date_obj.strftime("%d/%m/%Y %H:%M")

                except Exception as e:
                    logger.error(
                        f"[{self.account_index}] Failed to parse creation date: {e}"
                    )
            
            if username:
                logger.success(
                    f"[{self.account_index}] Successfully retrieved username: {username} | Account created at: {formatted_created_at}"
                )
                return username
            else:
                logger.error(
                    f"[{self.account_index}] Failed to get username: {response.text}"
                )
                return None

        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to get username: {e}")
            raise e

    async def _verify_error_response(self, response_text: str):
        if "this account is temporarily locked" in response_text:
            logger.error(f"[{self.account_index}] Account is temporarily locked")
            self.account_status = "locked"
            return False

        if "Could not authenticate you" in response_text:
            logger.error(f"[{self.account_index}] Account is not authenticated")
            self.account_status = "wrong_token"
            return False

        if "to protect our users from spam" in response_text:
            logger.error(f"[{self.account_index}] Account is Suspended")
            self.account_status = "suspended"
            return False

        logger.error(f"[{self.account_index}] Unknown error: {response_text}")
        self.account_status = "unknown"
        return True

    async def _update_cookies(self):
        try:
            ct0 = None
            for cookie_name, cookie_value in self.session.cookies.get_dict().items():
                if cookie_name == "ct0":
                    ct0 = cookie_value
                    break

            if not ct0:
                raise Exception("ct0 cookie not found")

            # Очищаем куки и устанавливаем нужные
            self.session.cookies.clear()
            self.session.cookies.set("auth_token", self.auth_token)
            self.session.cookies.set("ct0", ct0)

            # Обновляем заголовок x-csrf-token
            self.session.headers["x-csrf-token"] = ct0
            self.csrf_token = ct0

            return True
        except Exception as e:
            logger.error(f"[{self.account_index}] Failed to update cookies: {e}")
            return False
