"""reddit OAuth drop-in.

reddit API docs:
https://github.com/reddit-archive/reddit/wiki/API
https://www.reddit.com/dev/apis
praw API docs:
https://praw.readthedocs.io/en/v3.6.0/pages/oauth.html
"""
import logging

from google.cloud import ndb
import praw
from webob import exc

from . import handlers, models
from .webutil import handlers as webutil_handlers
from .webutil import util
from .webutil.util import json_dumps, json_loads

from random import randint

REDDIT_APP_KEY = util.read('reddit_app_key')
REDDIT_APP_SECRET = util.read('reddit_app_secret')

class RedditAuth(models.BaseAuth):
  """An authenticated reddit user.

  Provides methods that return information about this user and make OAuth-signed
  requests to the Tumblr API. Stores OAuth credentials in the datastore. See
  models.BaseAuth for usage details.

  reddit-specific details: implements "access_token," which is really a refresh_token
  see: https://stackoverflow.com/questions/28955541/how-to-get-access-token-reddit-api
  The datastore entity key name is the reddit username.
  """
  # access token
  refresh_token = ndb.StringProperty(required=True)
  user_json = ndb.TextProperty()
  
  def site_name(self):
    return 'reddit'

  def user_display_name(self):
    """Returns the username.
    """
    return self.key_id()

  def access_token(self):
    """Returns the OAuth refresh token.
    """
    return self.refresh_token


class StartHandler(handlers.StartHandler):
  """Starts reddit auth. goes directly to redirect. passes to_path in "state"
  """
  NAME = 'reddit'
  LABEL = 'Reddit'

  def redirect_url(self, state=None):
    # if state is None the reddit API redirect breaks, set to random string
    if not state:
      state = str(randint(100000,999999))
    assert REDDIT_APP_KEY and REDDIT_APP_SECRET, \
      "Please fill in the reddit_app_key and reddit_app_secret files in your app's root directory."
    reddit = praw.Reddit(client_id=REDDIT_APP_KEY,
                         client_secret=REDDIT_APP_SECRET,
                         redirect_uri=self.request.host_url + self.to_path,
                         user_agent='oauth-dropin reddit identity checker')
    
    # store the state for later use in the callback handler
    models.OAuthRequestToken(id=state,
                             token_secret=state,
                             state=state).put()
    st = util.encode_oauth_state({'state':state,'to_path':self.to_path})
    return reddit.auth.url(['identity'], st, 'permanent')

  @classmethod
  def button_html(cls, *args, **kwargs):
    return super(cls, cls).button_html(
      *args,
      input_style='background-color: #CEE3F8; padding: 10px',
      **kwargs)


class CallbackHandler(handlers.CallbackHandler):
  """OAuth callback. Only ensures that identity access was granted.
  """

  def get(self):
    error = self.request.get('error')
    st = util.decode_oauth_state(self.request.get('state'))
    state = st.get('state')
    to_path = st.get('to_path')
    code = self.request.get('code')
    if error or not state or not code:
      if error in ('access_denied'): 
        logging.info('User declined: %s', self.request.get('error_description')) 
        self.finish(None, state=state) 
        return 
      else: 
        msg = 'Error: %s' % (error) 
        logging.info(msg) 
        raise exc.HTTPBadRequest(msg) 
      
    # look up the stored state to check authenticity
    request_token = models.OAuthRequestToken.get_by_id(state)
    if request_token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % request_token_key)

    reddit = praw.Reddit(client_id=REDDIT_APP_KEY,
                         client_secret=REDDIT_APP_SECRET,
                         redirect_uri=self.request.host_url + to_path,
                         user_agent='oauth-dropin reddit identity checker')

    refresh_token = reddit.auth.authorize(code)
    user = reddit.user.me()
    # a short list of attributes to grab 
    # looks like calling json_dumps on the object opens some kind of stream
    # https://github.com/praw-dev/praw/blob/master/praw/models/reddit/redditor.py#L41
    attribute_list = ['name',
                      'comment_karma',
                      'created_utc',
                      'id',
                      'name',
                      'link_karma',
                      'icon_img']
    user_json = {a:getattr(user,a) for a in attribute_list}
    user_id = str(user)
    
    auth = RedditAuth(id=user_id,
                      refresh_token=refresh_token,
                      user_json=json_dumps(user_json))
    auth.put()
    self.finish(auth, state=st)