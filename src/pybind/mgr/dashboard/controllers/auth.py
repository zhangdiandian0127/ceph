# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging

import cherrypy

from .. import mgr
from ..exceptions import InvalidCredentialsError, UserDoesNotExist
from ..services.auth import AuthManager, JwtManager
from ..settings import Settings
from . import ApiController, ControllerDoc, EndpointDoc, RESTController, allow_empty_body

logger = logging.getLogger('controllers.auth')

AUTH_CHECK_SCHEMA = {
    "username": (str, "Username"),
    "permissions": ({
        "cephfs": ([str], "")
    }, "List of permissions acquired"),
    "sso": (bool, "Uses single sign on?"),
    "pwdUpdateRequired": (bool, "Is password update required?")
}


@ApiController('/auth', secure=False)
@ControllerDoc("Initiate a session with Ceph", "Auth")
class Auth(RESTController):
    """
    Provide authenticates and returns JWT token.
    """
    def create(self, username, password):
        user_data = AuthManager.authenticate(username, password)
        user_perms, pwd_expiration_date, pwd_update_required = None, None, None
        max_attempt = Settings.ACCOUNT_LOCKOUT_ATTEMPTS
        if max_attempt == 0 or mgr.ACCESS_CTRL_DB.get_attempt(username) < max_attempt:
            if user_data:
                user_perms = user_data.get('permissions')
                pwd_expiration_date = user_data.get('pwdExpirationDate', None)
                pwd_update_required = user_data.get('pwdUpdateRequired', False)

            if user_perms is not None:
                logger.info('Login successful: %s', username)
                mgr.ACCESS_CTRL_DB.reset_attempt(username)
                mgr.ACCESS_CTRL_DB.save()
                token = JwtManager.gen_token(username)
                token = token.decode('utf-8')
                cherrypy.response.headers['Authorization'] = "Bearer: {}".format(token)
                return {
                    'token': token,
                    'username': username,
                    'permissions': user_perms,
                    'pwdExpirationDate': pwd_expiration_date,
                    'sso': mgr.SSO_DB.protocol == 'saml2',
                    'pwdUpdateRequired': pwd_update_required
                }
            mgr.ACCESS_CTRL_DB.increment_attempt(username)
            mgr.ACCESS_CTRL_DB.save()
        else:
            try:
                user = mgr.ACCESS_CTRL_DB.get_user(username)
                user.enabled = False
                mgr.ACCESS_CTRL_DB.save()
                logging.warning('Maximum number of unsuccessful log-in attempts '
                                '(%d) reached for '
                                'username "%s" so the account was blocked. '
                                'An administrator will need to re-enable the account',
                                max_attempt, username)
                raise InvalidCredentialsError
            except UserDoesNotExist:
                raise InvalidCredentialsError
        logger.info('Login failed: %s', username)
        raise InvalidCredentialsError

    @RESTController.Collection('POST')
    @allow_empty_body
    def logout(self):
        logger.debug('Logout successful')
        token = JwtManager.get_token_from_header()
        JwtManager.blocklist_token(token)
        redirect_url = '#/login'
        if mgr.SSO_DB.protocol == 'saml2':
            redirect_url = 'auth/saml2/slo'
        return {
            'redirect_url': redirect_url
        }

    def _get_login_url(self):
        if mgr.SSO_DB.protocol == 'saml2':
            return 'auth/saml2/login'
        return '#/login'

    @RESTController.Collection('POST', query_params=['token'])
    @EndpointDoc("Check token Authentication",
                 parameters={'token': (str, 'Authentication Token')},
                 responses={201: AUTH_CHECK_SCHEMA})
    def check(self, token):
        if token:
            user = JwtManager.get_user(token)
            if user:
                return {
                    'username': user.username,
                    'permissions': user.permissions_dict(),
                    'sso': mgr.SSO_DB.protocol == 'saml2',
                    'pwdUpdateRequired': user.pwd_update_required
                }
        return {
            'login_url': self._get_login_url(),
        }
