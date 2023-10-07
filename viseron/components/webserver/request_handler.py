"""Viseron request handler."""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, overload

import tornado.web
from tornado.ioloop import IOLoop

from viseron.components.webserver.const import COMPONENT
from viseron.const import DOMAIN_FAILED
from viseron.domains.camera import FailedCamera
from viseron.domains.camera.const import DOMAIN as CAMERA_DOMAIN
from viseron.exceptions import DomainNotRegisteredError

if TYPE_CHECKING:
    from viseron import Viseron
    from viseron.components.webserver import Webserver
    from viseron.components.webserver.auth import RefreshToken, User
    from viseron.domains.camera import AbstractCamera

LOGGER = logging.getLogger(__name__)


class ViseronRequestHandler(tornado.web.RequestHandler):
    """Base request handler."""

    def initialize(self, vis: Viseron) -> None:
        """Initialize request handler."""
        self._vis = vis
        self._webserver: Webserver = vis.data[COMPONENT]
        self.current_user = None
        # Manually set xsrf cookie
        self.xsrf_token  # pylint: disable=pointless-statement

    async def run_in_executor(self, func, *args):
        """Run function in executor."""
        return await IOLoop.current().run_in_executor(None, func, *args)

    async def prepare(self) -> None:    # pylint: disable=invalid-overridden-method
        """Prepare request handler.

        get_current_user cannot be async, so we set self.current_user here.
        """
        if not self._webserver.auth:
            return

        if _user := self.get_cookie("user"):
            self.current_user = await self.run_in_executor(
                self._webserver.auth.get_user, _user
            )

    @property
    def current_user(self) -> User | None:
        """Return current user."""
        return super().current_user

    @current_user.setter
    def current_user(self, value: User | None) -> None:
        self._current_user = value

    @property
    def webserver(self) -> Webserver:
        """Return the webserver component."""
        return self._webserver

    @property
    def status(self):
        """Return the status of the request."""
        return self.get_status()

    def on_finish(self) -> None:
        """Log requests with failed authentication."""
        if self.status == HTTPStatus.UNAUTHORIZED:
            LOGGER.warning(
                f"Request with failed authentication from {self.request.remote_ip} for"
                f" URL: {self.request.uri} {self.request.headers.get('User-Agent')}",
            )

    def set_cookies(
        self,
        refresh_token: RefreshToken,
        access_token: str,
        user: User,
        new_session=False,
    ) -> None:
        """Set session cookies."""
        now = datetime.utcnow()

        _header, _payload, signature = access_token.split(".")

        expires = (
            now + self._webserver.auth.session_expiry
            if self._webserver.auth.session_expiry
            else now + timedelta(days=3650)
        )
        # Refresh all cookies on every request if expiry is None because you can't have
        # infinite cookies in some browsers
        if new_session or self._webserver.auth.session_expiry is None:
            self.clear_cookie("refresh_token")
            self.set_secure_cookie(
                "refresh_token",
                refresh_token.token,
                expires=expires,
                httponly=True,
                samesite="strict",
                secure=self.request.protocol == "https",
            )
            self.clear_cookie("static_asset_key")
            self.set_secure_cookie(
                "static_asset_key",
                refresh_token.static_asset_key,
                expires=expires,
                httponly=True,
                samesite="strict",
                secure=self.request.protocol == "https",
            )
            self.clear_cookie("user")
            self.set_cookie(
                "user",
                user.id,
                expires=expires,
                samesite="strict",
                secure=self.request.protocol == "https",
            )
        self.clear_cookie("signature_cookie")
        self.set_secure_cookie(
            "signature_cookie",
            signature,
            expires=expires,
            httponly=True,
            samesite="strict",
            secure=self.request.protocol == "https",
        )

    def clear_all_cookies(self, path: str = "/", domain: str | None = None) -> None:
        """Overridden clear_all_cookies.

        Clears all cookies except for the XSRF cookie.
        """
        for name in self.request.cookies:
            if name == "_xsrf":
                continue
            self.clear_cookie(name, path=path, domain=domain)

    def validate_access_token(
        self, access_token: str, check_refresh_token: bool = True
    ):
        """Validate access token."""
        # Check access token is valid
        refresh_token = self._webserver.auth.validate_access_token(access_token)
        if refresh_token is None:
            LOGGER.debug("Access token not valid")
            return False

        # Check refresh_token cookie exists
        if check_refresh_token:
            refresh_token_cookie = self.get_secure_cookie("refresh_token")
            if refresh_token_cookie is None:
                LOGGER.debug("Refresh token is missing")
                return
            if not hmac.compare_digest(
                refresh_token_cookie.decode(), refresh_token.token
            ):
                LOGGER.debug("Access token does not belong to the refresh token.")
                return False

        user = self._webserver.auth.get_user(refresh_token.user_id)
        if user is None or not user.enabled:
            LOGGER.debug("User not found or disabled")
            return False

        if self.current_user != user:
            LOGGER.debug("User mismatch")
            return False

        return True

    def _get_cameras(self):
        """Get all registered camera instances."""
        try:
            return self._vis.get_registered_identifiers(CAMERA_DOMAIN)
        except DomainNotRegisteredError:
            return None

    @overload
    def _get_camera(self, camera_identifier: str) -> AbstractCamera | None:
        ...

    @overload
    def _get_camera(
        self, camera_identifier: str, failed: Literal[False]
    ) -> AbstractCamera | None:
        ...

    @overload
    def _get_camera(
        self, camera_identifier: str, failed: Literal[True]
    ) -> AbstractCamera | FailedCamera | None:
        ...

    @overload
    def _get_camera(
        self, camera_identifier: str, failed: bool
    ) -> AbstractCamera | FailedCamera | None:
        ...

    def _get_camera(self, camera_identifier: str, failed: bool = False):
        """Get camera instance.

        If failed is True, check for failed camera instances
        if the camera is not found.
        """
        camera = None
        try:
            camera = self._vis.get_registered_domain(CAMERA_DOMAIN, camera_identifier)
        except DomainNotRegisteredError:
            if failed:
                if domain_to_setup := (
                    self._vis.data[DOMAIN_FAILED]
                    .get(CAMERA_DOMAIN, {})
                    .get(camera_identifier, None)
                ):
                    camera = domain_to_setup.error_instance
        return camera

    def validate_camera_token(self, camera: AbstractCamera) -> bool:
        """Validate camera token."""
        if access_token := self.get_argument("access_token", None, strip=True):
            return access_token in camera.access_tokens
        # Access token query parameter not set, check cookies
        refresh_token_cookie = self.get_secure_cookie("refresh_token")
        static_asset_key = self.get_secure_cookie("static_asset_key")
        if refresh_token_cookie and static_asset_key:
            refresh_token = self._webserver.auth.get_refresh_token_from_token(
                refresh_token_cookie.decode()
            )
            if hmac.compare_digest(
                refresh_token.static_asset_key, static_asset_key.decode()
            ):
                return True
        return False
