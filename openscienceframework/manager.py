# -*- coding: utf-8 -*-
"""
@author: Daniel Schreij

This module is distributed under the Apache v2.0 License.
You should have received a copy of the Apache v2.0 License
along with this module. If not, see <http://www.apache.org/licenses/>.
"""
# Python3 compatibility
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

# Import basics
import logging
import os
import json
import time

#OSF modules
import openscienceframework.connection as osf
from openscienceframework import widgets, events, loginwindow

# Easier function decorating
from functools import wraps

# PyQt modules
from qtpy import QtCore, QtNetwork, QtWidgets
import qtpy

# Python warnings
import warnings

# Python 2 and 3 compatiblity settings
from openscienceframework.compat import *

class ConnectionManager(QtNetwork.QNetworkAccessManager):
	"""
	The connection manager does much of the heavy lifting in communicating with the
	OSF. It checks if the app is still authorized to send requests, and also checks
	for responses indicating this is not the case."""

	# The maximum number of allowed redirects
	MAX_REDIRECTS = 5
	error_message = QtCore.pyqtSignal('QString','QString')
	info_message = QtCore.pyqtSignal('QString','QString')

	def __init__(self, manager, tokenfile="token.json"):
		""" Constructor """
		super(ConnectionManager, self).__init__()
		self.manager = manager
		self.tokenfile = tokenfile
		self.dispatcher = events.EventDispatcher()

		# Notifications
		self.notifier = events.Notifier()
		self.error_message.connect(self.notifier.error)
		self.info_message.connect(self.notifier.info)

		# Init browser in which login page is displayed
		self.browser = loginwindow.LoginWindow()
		# Connect browsers logged in event to that of dispatcher's
		self.browser.logged_in.connect(self.dispatcher.dispatch_login)

		self.logged_in_user = {}

	#--- Login and Logout functions

	def login(self):
		""" Opens a browser window through which the user can log in. Upon successful
		login, the browser widgets fires the 'logged_in' event. which is caught by this object
		again in the handle_login() function. """

		# If a valid stored token is found, read that in an dispatch login event
		if self.check_for_stored_token(self.tokenfile):
			self.dispatcher.dispatch_login()
			return
		# Otherwise, do the whole authentication dance
		self.show_login_window()

	def show_login_window(self):
		""" Show the QWebView window with the login page of OSF """
		auth_url, state = osf.get_authorization_url()

		# Set up browser
		browser_url = get_QUrl(auth_url)

		self.browser.load(browser_url)
		self.browser.show()

	def logout(self):
		""" Logout from OSF """
		if osf.is_authorized() and osf.session.access_token:
			self.post(
				osf.logout_url,
				{'token':osf.session.access_token},
				self.__logout_succeeded
			)

	def __logout_succeeded(self,data,*args):
		self.dispatcher.dispatch_logout()

	def check_for_stored_token(self, tokenfile):
		""" Checks if valid token information is stored in a token.json file.
		of the project root. If not, or if the token is invalid/expired, it returns
		False"""

		logging.info("Looking for token at {}".format(tokenfile))

		if not os.path.isfile(tokenfile):
			return False

		try:
			token = json.load(open(tokenfile))
		except IOError:
			raise IOError("Token file could not be opened.")

		# Check if token has not yet expired
		if token["expires_at"] > time.time() :
			# Load the token information in the session object, but check its
			# validity!
			osf.session.token = token
			# See if a request succeeds without errors
			try:
				osf.get_logged_in_user()
				return True
			except osf.TokenExpiredError:
				osf.reset_session()
				os.remove(tokenfile)
				self.show_login_window()
		else:
			logging.info("Token expired; need log-in")
			return False

	#--- Communication with OSF API

	def check_network_accessibility(func):
		""" Checks if network is accessible """
		@wraps(func)
		def func_wrapper(inst, *args, **kwargs):
			if inst.networkAccessible() == inst.NotAccessible:
				self.error_message.emit(
					"No network access",
					"Your network connection is down or you currently have"
					" no Internet access."
				)
				return
			else:
				return func(inst, *args, **kwargs)
		return func_wrapper

	def add_token(self,request):
		""" Adds the OAuth2 token to the pending HTTP request (if available).

		Parameters
		----------
		request : QtNetwork.QNetworkRequest
			The network request item in whose header to add the OAuth2 token
		"""
		if osf.is_authorized():
			name = safe_encode("Authorization")
			value = safe_encode("Bearer {}".format(osf.session.access_token))
			request.setRawHeader(name, value)
			return True
		else:
			return False

	### Basic HTTP Functions

	@check_network_accessibility
	def get(self, url, callback, *args, **kwargs):
		""" Perform a HTTP GET request. The OAuth2 token is automatically added to the
		header if the request is going to an OSF server.

		Parameters
		----------
		url : string / QtCore.QUrl
			The target url/endpoint to perform the GET request on
		callback : function
			The function to call once the request is finished successfully.
		downloadProgess : function (defualt: None)
			The slot (callback function) for the downloadProgress signal of the
			reply object. This signal is emitted after a certain amount of bytes
			is received, and can be used for instance to update a download progress
			dialog box. The callback function should have two parameters to which
			the transfered and total bytes can be assigned.
		readyRead : function (default : None)
			The slot (callback function) for the readyRead signal of the
			reply object.
		errorCallback : function (default: None)
			function to call whenever an error occurs. Should be able to accept
			the reply object as an argument. This function is also called if the
			operation is aborted by the user him/herself.
		progressDialog : QtWidgets.QProgressDialog (default: None)
			The dialog to send the progress indication to. Will be included in the
			reply object so that it is accessible in the downloadProgress slot, by
			calling self.sender().property('progressDialog')
		abortSignal : QtCore.pyqtSignal
			This signal will be attached to the reply objects abort() slot, so that
			the operation can be aborted from outside if necessary.
		*args (optional)
			Any other arguments that you want to have passed to the callback
		**kwargs (optional)
			Any other keywoard arguments that you want to have passed to the callback
		"""
		# First do some checking of the passed arguments

		if not type(url) == QtCore.QUrl and not isinstance(url, basestring):
			raise TypeError("url should be a string or QUrl object")

		if not callable(callback):
			raise TypeError("callback should be a function or callable.")

		if not type(url) is QtCore.QUrl:
			url = get_QUrl(url)

		request = QtNetwork.QNetworkRequest(url)

		# Add OAuth2 token
		if not self.add_token(request):
			warnings.warn("Token could not be added to the request")
			
		# Check if this is a redirect and keep a count to prevent endless
		# redirects. If redirect_count is not set, init it to 0
		kwargs['redirect_count'] = kwargs.get('redirect_count',0)

		reply = super(ConnectionManager, self).get(request)

		# If provided, connect the abort signal to the reply's abort() slot
		abortSignal = kwargs.get('abortSignal', None)
		if not abortSignal is None:
			abortSignal.connect(reply.abort)

		# Check if a QProgressDialog has been passed to which the download status
		# can be reported. If so, add it as a property of the reply object
		progressDialog = kwargs.get('progressDialog', None)
		if isinstance(progressDialog, QtWidgets.QProgressDialog):
			progressDialog.canceled.connect(reply.abort)
			reply.setProperty('progressDialog', progressDialog)

		# Check if a callback has been specified to which the downloadprogress
		# is to be reported
		dlpCallback = kwargs.get('downloadProgress', None)
		if callable(dlpCallback):
			reply.downloadProgress.connect(dlpCallback)

		# Check if a callback has been specified for reply's readyRead() signal
		# which emits as soon as data is available on the buffer and doesn't wait
		# till the whole transfer is finished as the finished() callback does
		# This is useful when downloading larger files
		rrCallback = kwargs.get('readyRead', None)
		if callable(rrCallback):
			reply.readyRead.connect(
				lambda: rrCallback(*args, **kwargs)
			)

		reply.finished.connect(
			lambda: self.__reply_finished(
				callback, *args, **kwargs
			)
		)
		return reply

	@check_network_accessibility
	def post(self, url, data_to_send, callback, *args, **kwargs):
		""" Perform a HTTP POST request. The OAuth2 token is automatically added to the
		header if the request is going to an OSF server. This request is mainly used to send
		small amounts of data to the OSF framework (use PUT for larger files, as this is also
		required by the WaterButler service used for OSF)

		Parameters
		----------
		url : string / QtCore.QUrl
			The target url/endpoint to perform the POST request on.
		data_to_send : dict
			The data to send with the POST request. keys will be used as variable names
			and values will be used as the variable values.
		callback : function
			The function to call once the request is finished.
		*args (optional)
			Any other arguments that you want to have passed to callable.
		"""
		# First do some checking of the passed arguments
		if not type(url) == QtCore.QUrl and not isinstance(url, basestring):
			raise TypeError("url should be a string or QUrl object")

		if not callable(callback):
			raise TypeError("callback should be a function or callable.")

		if not type(data_to_send) is dict:
			raise TypeError("The POST data should be passed as a dict")

		if not type(url) is QtCore.QUrl:
			url = get_QUrl(url)

		request = QtNetwork.QNetworkRequest(url)
		request.setHeader(request.ContentTypeHeader,"application/x-www-form-urlencoded");

#		logging.info("POST {}".format(url))

		# Add OAuth2 token
		self.add_token(request)

		# Sadly, Qt4 and Qt5 show some incompatibility in that QUrl no longer has the
		# addQueryItem function in Qt5. This has moved to a differen QUrlQuery object
		if QtCore.QT_VERSION_STR < '5':
			postdata = QtCore.QUrl()
		else:
			postdata = QtCore.QUrlQuery()
		# Add data
		for varname in data_to_send:
			postdata.addQueryItem(varname, data_to_send.get(varname))
		# Convert to QByteArray for transport
		if QtCore.QT_VERSION_STR < '5':
			final_postdata = postdata.encodedQuery()
		else:
			final_postdata = safe_encode(postdata.toString(QtCore.QUrl.FullyEncoded))
		# Fire!
		reply = super(ConnectionManager, self).post(request, final_postdata)
		reply.finished.connect(lambda: self.__reply_finished(callback, *args, **kwargs))

	@check_network_accessibility
	def put(self, url, data_to_send, callback, *args, **kwargs):
		""" Perform a HTTP PUT request. The OAuth2 token is automatically added to the
		header if the request is going to an OSF server. This method should be used
		to upload larger sets of data such as files.

		Parameters
		----------
		url : string / QtCore.QUrl
			The target url/endpoint to perform the PUT request on.
		data_to_send : QIODevice
			The file to upload (QFile or other QIODevice type)
		callback : function
			The function to call once the request is finished.
		uploadProgess : function (defualt: None)
			The slot (callback function) for the downloadProgress signal of the
			reply object. This signal is emitted after a certain amount of bytes
			is received, and can be used for instance to update a download progress
			dialog box. The callback function should have two parameters to which
			the transfered and total bytes can be assigned.
		errorCallback : function (default: None)
			function to call whenever an error occurs. Should be able to accept
			the reply object as an argument. This function is also called if the
			operation is aborted by the user him/herself.
		progressDialog : QtWidgets.QProgressDialog (default: None)
			The dialog to send the progress indication to. Will be included in the
			reply object so that it is accessible in the downloadProgress slot, by
			calling self.sender().property('progressDialog')
		abortSignal : QtCore.pyqtSignal
			This signal will be attached to the reply objects abort() slot, so that
			the operation can be aborted from outside if necessary.
		*args (optional)
			Any other arguments that you want to have passed to the callback
		"""
		# First do some checking of the passed arguments
		if not type(url) == QtCore.QUrl and not isinstance(url, basestring):
			raise TypeError("url should be a string or QUrl object")

		if not callable(callback):
			raise TypeError("callback should be a function or callable.")

		if not isinstance(data_to_send, QtCore.QIODevice):
			raise TypeError("The data_to_send should be of type QtCore.QIODevice")

		if not type(url) is QtCore.QUrl:
			url = get_QUrl(url)

		request = QtNetwork.QNetworkRequest(url)
		# request.setHeader(request.ContentTypeHeader,"application/x-www-form-urlencoded");

#		logging.info("PUT {}".format(url))

		# Add OAuth2 token
		self.add_token(request)

		reply = super(ConnectionManager, self).put(request, data_to_send)
		reply.finished.connect(lambda: self.__reply_finished(callback, *args, **kwargs))

		# Check if a QProgressDialog has been passed to which the download status
		# can be reported. If so, add it as a property of the reply object
		progressDialog = kwargs.get('progressDialog', None)
		if isinstance(progressDialog, QtWidgets.QProgressDialog):
			progressDialog.canceled.connect(reply.abort)
			reply.setProperty('progressDialog', progressDialog)
		else:
			logging.error("progressDialog is not a QtWidgets.QProgressDialog")

		# If provided, connect the abort signal to the reply's abort() slot
		abortSignal = kwargs.get('abortSignal', None)
		if not abortSignal is None:
			abortSignal.connect(reply.abort)

		# Check if a callback has been specified to which the downloadprogress
		# is to be reported
		ulpCallback = kwargs.get('uploadProgress', None)
		if callable(ulpCallback):
			reply.uploadProgress.connect(ulpCallback)

	@check_network_accessibility
	def delete(self, url, callback, *args, **kwargs):
		""" Perform a HTTP DELETE request. The OAuth2 token is automatically added to the
		header if the request is going to an OSF server.

		Parameters
		----------
		url : string / QtCore.QUrl
			The target url/endpoint to perform the GET request on
		callback : function
			The function to call once the request is finished successfully.
		errorCallback : function (default: None)
			function to call whenever an error occurs. Should be able to accept
			the reply object as an argument. This function is also called if the
			operation is aborted by the user him/herself.
		abortSignal : QtCore.pyqtSignal
			This signal will be attached to the reply objects abort() slot, so that
			the operation can be aborted from outside if necessary.
		*args (optional)
			Any other arguments that you want to have passed to the callback
		**kwargs (optional)
			Any other keywoard arguments that you want to have passed to the callback
		"""
		# First do some checking of the passed arguments

		if not type(url) == QtCore.QUrl and not isinstance(url, basestring):
			raise TypeError("url should be a string or QUrl object")

		if not callable(callback):
			raise TypeError("callback should be a function or callable.")

		if not type(url) is QtCore.QUrl:
			url = get_QUrl(url)

		request = QtNetwork.QNetworkRequest(url)

		logging.info("GET {}".format(url))

		# Add OAuth2 token
		self.add_token(request)

		# Check if this is a redirect and keep a count to prevent endless
		# redirects. If redirect_count is not set, init it to 0
		kwargs['redirect_count'] = kwargs.get('redirect_count',0)

		reply = super(ConnectionManager, self).deleteResource(request)

		# If provided, connect the abort signal to the reply's abort() slot
		abortSignal = kwargs.get('abortSignal', None)
		if not abortSignal is None:
			abortSignal.connect(reply.abort)

		reply.finished.connect(
			lambda: self.__reply_finished(
				callback, *args, **kwargs
			)
		)
		return reply

	### Convenience HTTP Functions

	def get_logged_in_user(self, callback, *args, **kwargs):
		""" Contact the OSF to request data of the currently logged in user

		Parameters
		----------
		callback : function
			The callback function to which the data should be delivered once the
			request is finished

		Returns
		-------
		QtNetwork.QNetworkReply or None if something went wrong
		"""
		api_call = osf.api_call("logged_in_user")
		return self.get(api_call, callback, *args, **kwargs)

	def get_user_projects(self, callback, *args, **kwargs):
		""" Get a list of projects owned by the currently logged in user from OSF

		Parameters
		----------
		callback : function
			The callback function to which the data should be delivered once the
			request is finished

		Returns
		-------
		QtNetwork.QNetworkReply or None if something went wrong
		"""
		api_call = osf.api_call("projects")
		return self.get(api_call, callback, *args, **kwargs)

	def get_project_repos(self, project_id, callback, *args, **kwargs):
		""" Get a list of repositories from the OSF that belong to the passed
		project id

		Parameters
		----------
		project_id : string
			The project id that OSF uses for this project (e.g. the node id)
		callback : function
			The callback function to which the data should be delivered once the
			request is finished

		Returns
		-------
		QtNetwork.QNetworkReply or None if something went wrong
		"""
		api_call = osf.api_call("project_repos", project_id)
		return self.get(api_call, callback, *args, **kwargs)

	def get_repo_files(self, project_id, repo_name, callback, *args, **kwargs):
		""" Get a list of files from the OSF that belong to the indicated
		repository of the passed project id

		Parameters
		----------
		project_id : string
			The project id that OSF uses for this project (e.g. the node id)
		repo_name : string
			The repository to get the files from. Should be something along the
			lines of osfstorage, github, dropbox, etc. Check OSF documentation
			for a full list of specifications.
		callback : function
			The callback function to which the data should be delivered once the
			request is finished

		Returns
		-------
		QtNetwork.QNetworkReply or None if something went wrong
		"""
		api_call = osf.api_call("repo_files",project_id, repo_name)
		return self.get(api_call, callback, *args, **kwargs)

	def get_file_info(self, file_id, callback, *args, **kwargs):
		""" Get a list of files from the OSF that belong to the indicated
		repository of the passed project id

		Parameters
		----------
		file_id : string
			The OSF file identifier (e.g. the node id).
		callback : function
			The callback function to which the data should be delivered once the
			request is finished

		Returns
		-------
		QtNetwork.QNetworkReply or None if something went wrong
		"""
		api_call = osf.api_call("file_info",file_id)
		return self.get(api_call, callback, *args, **kwargs)

	def download_file(self, url, destination, *args, **kwargs):
		""" Download a file by a using HTTP GET request. The OAuth2 token is automatically
		added to the header if the request is going to an OSF server.

		Parameters
		----------
		url : string / QtCore.QUrl
			The target url that points to the file to download
		destination : string
			The path and filename with which the file should be saved.
		finished_callback : function (default: None)
			The function to call once the download is finished.
		downloadProgress : function (default: None)
			The slot (callback function) for the downloadProgress signal of the
			reply object. This signal is emitted after a certain amount of bytes
			is received, and can be used for instance to update a download progress
			dialog box. The callback function should have two parameters to which
			the transfered and total bytes can be assigned.
		errorCallback : function (default: None)
			function to call whenever an error occurs. Should be able to accept
			the reply object as an argument.
		progressDialog : QtWidgets.QProgressDialog (default : None)
			The dialog to send the progress indication to. Will be included in the
			reply object so that it is accessible from the downloadProgress slot
		"""

		# Check if destination is a string
		if not type(destination) == str:
			raise ValueError("destination should be a string")
		# Check if the specified folder exists. However, because a situation is possible in which
		# the user has selected a destination but deletes the folder in some other program in the meantime,
		# show a message box, but do not raise an exception, because we don't want this to completely crash
		# our program.
		if not os.path.isdir(os.path.split(os.path.abspath(destination))[0]):
			self.error_message.emit(_("{} is not a valid destination").format(destination))
			return
		kwargs['destination'] = destination

		# Create tempfile
		tmp_file = QtCore.QTemporaryFile()
		tmp_file.open(QtCore.QIODevice.WriteOnly)
		kwargs['tmp_file'] = tmp_file

		# Callback function for when bytes are received
		kwargs['readyRead'] = self.__download_readyRead
		self.get(url, self.__download_finished, *args, **kwargs)

	def upload_file(self, url, source_file, *args, **kwargs):
		""" Upload a file to the specified destination on the OSF

		Parameters
		----------
		url : string / QtCore.QUrl
			The target url that points to endpoint handling the upload
		source : string / QtCore.QtFile
			The path and file which should be uploaded.
		finishedCallback : function (default: None)
			The function to call once the upload is finished.
		uploadProgress : function (default: None)
			The slot (callback function) for the uploadProgress signal of the
			reply object. This signal is emitted after a certain amount of bytes
			is received, and can be used for instance to update a upload progress
			dialog box. The callback function should have two parameters to which
			the transfered and total bytes can be assigned.
		errorCallback : function (default: None)
			function to call whenever an error occurs. Should be able to accept
			the reply object as an argument.
		progressDialog : QtWidgets.QProgressDialog (default : None)
			The dialog to send the progress indication to. Will be included in the
			reply object so that it is accessible from the downloadProgress slot
		"""
		# Put checks for the url to be a string or QUrl

		# Check source file
		if isinstance(source_file, basestring):
			# Check if the specified file exists, because a situation is possible in which
			# the user has deleted the file in the meantime in another program.
			# show a message box, but do not raise an exception, because we don't want this
			# to completely crash our program.
			if not os.path.isfile(os.path.abspath(source_file)):
				self.error_message.emit(_("{} is not a valid source file").format(destination))
				return

			# Open source file for reading
			source_file = QtCore.QFile(source_file)
		elif not isinstance(source_file, QtCore.QIODevice):
			self.error_message.emit(_("{} is not a string or QIODevice instance").format(destination))
			return

		source_file.open(QtCore.QIODevice.ReadOnly)
		kwargs['source_file'] = source_file
		self.put(url, source_file, self.__upload_finished, *args, **kwargs)

	#--- PyQt Slots

	def __reply_finished(self, callback, *args, **kwargs):
		reply = self.sender()
		request = reply.request()

		errorCallback = kwargs.get('errorCallback', None)

		# If an error occured, just show a simple QMessageBox for now
		if reply.error() != reply.NoError:
			# Don't show error notification if user manually cancelled operation.
			# This is undesirable most of the time, and when it is required, it
			# can be implemented by using the errorCallback function
			if reply.error() != reply.OperationCanceledError:
				self.error_message.emit(
					str(reply.attribute(request.HttpStatusCodeAttribute)),
					reply.errorString()
				)
			# User not authenticated to perform this request
			# Show login window again
			if reply.error() == reply.AuthenticationRequiredError:
				# If access is denied, the user's token must have expired
				# or something like that. Dispatch the logout signal and
				# show the login window again
				self.dispatcher.dispatch_logout()
				self.show_login_window()
				
			# Call error callback
			if callable(errorCallback):
				errorCallback(reply)
			reply.deleteLater()
			return

		# Check if the reply indicates a redirect
		if reply.attribute(request.HttpStatusCodeAttribute) in [301,302]:
			# To prevent endless redirects, make a count of them and only
			# allow a preset maximum
			if kwargs['redirect_count'] < self.MAX_REDIRECTS:
				kwargs['redirect_count'] += 1
			else:
				self.error_message.emit(
					_("Whoops, something is going wrong"),
					_("Too Many redirects")
				)
				if callable(errorCallback):
					errorCallback(reply)
				reply.deleteLater()
				return
			# Perform another request with the redirect_url and pass on the callback
			redirect_url = reply.attribute(request.RedirectionTargetAttribute)
			logging.info('Redirected to {}'.format(redirect_url))
			if reply.operation() == self.GetOperation:
				self.get(redirect_url, callback, *args, **kwargs)
			# TODO: implement this for POST, PUT and DELETE too
#			if reply.operation() == self.PostOperation:
#				self.post(redirect_url, callback, *args, **kwargs)
#			if reply.operation() == self.PutOperation:
#				self.put(redirect_url, callback, *args, **kwargs)
#			if reply.operation() == self.DeleteOperation:
#				self.delete(redirect_url, callback, *args, **kwargs)
		else:
			# Remove (potentially) internally used kwargs before passing
			# data on to the callback
			kwargs.pop('redirect_count', None)
			kwargs.pop('downloadProgress', None)
			kwargs.pop('uploadProgress', None)
			kwargs.pop('readyRead', None)
			kwargs.pop('errorCallback', None)
			kwargs.pop('abortSignal', None)
			callback(reply, *args, **kwargs)

		# Cleanup, mark the reply object for deletion
		reply.deleteLater()

	def __download_readyRead(self, *args, **kwargs):
		reply = self.sender()
		data = reply.readAll()
		if not 'tmp_file' in kwargs or not isinstance(kwargs['tmp_file'], QtCore.QTemporaryFile):
			raise AttributeError('Missing file handle to write to')
		kwargs['tmp_file'].write(data)

	def __download_finished(self, reply, *args, **kwargs):
		# Do some checks to see if the required data has been passed.
		if not 'destination' in kwargs:
			raise AttributeError("No destination passed")
		if not 'tmp_file' in kwargs or not isinstance(kwargs['tmp_file'], QtCore.QTemporaryFile):
			raise AttributeError("No valid reference to temp file where data was saved")

		kwargs['tmp_file'].close()
		# If a file with the same name already exists at the location, try to
		# delete it.
		if QtCore.QFile.exists(kwargs['destination']):
			if not QtCore.QFile.remove(kwargs['destination']):
				# If the destination file could not be deleted, notify the user
				# of this and stop the operation
				self.error_message.emit(
					_("Error saving file"),
					_("Could not replace {}").format(kwargs['destination'])
				)
				return
		# Copy the temp file to its destination
		if not kwargs['tmp_file'].copy(kwargs['destination']):
			self.error_message.emit(
				_("Error saving file"),
				_("Could not save file to {}").format(kwargs['destination'])
			)
			return

		fcb = kwargs.pop('finishedCallback',None)
		if callable(fcb):
			fcb(reply, *args, **kwargs)

	def __upload_finished(self, reply, *args, **kwargs):
		if not 'source_file' in kwargs or not isinstance(kwargs['source_file'], QtCore.QIODevice):
			raise AttributeError("No valid open file handle")
		# Close the source file
		kwargs['source_file'].close()

		# If another external callback function was provided, call it below
		fcb = kwargs.pop('finishedCallback',None)
		if callable(fcb):
			fcb(reply, *args, **kwargs)

	def handle_login(self):
		self.get_logged_in_user(self.set_logged_in_user)

	def handle_logout(self):
		self.osf.reset_session()
		self.logged_in_user = {}

	### Other callbacks

	def set_logged_in_user(self, user_data):
		""" Callback function - Locally saves the data of the currently logged_in user """
		self.logged_in_user = json.loads(safe_decode(user_data.readAll().data()))
