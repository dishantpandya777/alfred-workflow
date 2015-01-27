# encoding: utf-8
#
# Copyright (c) 2014 Dean Jackson <deanishe@deanishe.net>
#
# MIT Licence. See http://opensource.org/licenses/MIT
#
# Created on 2014-02-15
#

"""
The :class:`Workflow` object is the main interface to this library.

See :ref:`setup` in the :ref:`user-manual` for an example of how to set
up your Python script to best utilise the :class:`Workflow` object.

"""

from __future__ import print_function, unicode_literals, absolute_import

import binascii
import os
import sys
import re
import subprocess
import shutil
import time
import logging
import logging.handlers

from workflow import (
    base,
    env,
    hooks,
    icons,
    search,
    storage,
    util,
)

# TODO: Change this!
from workflow.storage import manager
from workflow.feedback import Item, XMLGenerator

log = base.get_logger(__name__)

####################################################################
# Used by `Workflow.check_update`
####################################################################

# Number of days to wait between checking for updates to the workflow
DEFAULT_UPDATE_FREQUENCY = 1


####################################################################
# Implementation classes
####################################################################

class Workflow(object):
    """Create new :class:`Workflow` instance.

        :param default_settings: default workflow settings. If no settings file
            exists, :class:`Workflow.settings` will be pre-populated with
            ``default_settings``.
        :type default_settings: :class:`dict`
        :param update_settings: settings for updating your workflow from GitHub.
            This must be a :class:`dict` that contains ``github_slug`` and
            ``version`` keys. ``github_slug`` is of the form ``username/repo``
            and ``version`` **must** correspond to the tag of a release.
            See :ref:`updates` for more information.
        :type update_settings: :class:`dict`
        :param input_encoding: encoding of command line arguments
        :type input_encoding: :class:`unicode`
        :param normalization: normalisation to apply to CLI args.
            See :meth:`Workflow.decode` for more details.
        :type normalization: :class:`unicode`
        :param capture_args: capture and act on ``workflow:*`` arguments. See
            :ref:`Magic arguments <magic-arguments>` for details.
        :type capture_args: :class:`Boolean`
        :param libraries: sequence of paths to directories containing
            libraries. These paths will be prepended to ``sys.path``.
        :type libraries: :class:`tuple` or :class:`list`
        :param help_url: URL to webpage where a user can ask for help with
            the workflow, report bugs, etc. This could be the GitHub repo
            or a page on AlfredForum.com. If your workflow throws an error,
            this URL will be displayed in the log and Alfred's debugger. It can
            also be opened directly in a web browser with the ``workflow:help``
            :ref:`magic argument <magic-arguments>`.
        :type help_url: :class:`unicode` or :class:`str`

    """

    # Which class to use to generate feedback items. You probably
    # won't want to change this
    item_class = Item

    def __init__(self, default_settings=None, update_settings=None,
                 input_encoding='utf-8', normalization='NFC',
                 capture_args=True, libraries=None,
                 help_url=None):

        self._default_settings = default_settings or {}
        self._update_settings = update_settings or {}
        self._input_encoding = input_encoding
        self._normalizsation = normalization
        self._capture_args = capture_args
        self.help_url = help_url
        self._xmlgen = XMLGenerator()
        self._workflowdir = None
        self._settings_path = None
        self._settings = None
        self._bundleid = None
        self._name = None
        self._cache_serializer = 'cpickle'
        self._data_serializer = 'cpickle'
        self._logger = None
        self._alfred_env = None
        # Stored Exception
        self.exception = None
        # Version number of the workflow
        self._version = base.UNSET
        # Version from last workflow run
        self._last_version_run = base.UNSET
        # Magic arguments
        #: The prefix for all magic arguments. Default is ``workflow:``
        self.magic_prefix = 'workflow:'
        #: Mapping of available magic arguments. The built-in magic
        #: arguments are registered by default. To add your own magic arguments
        #: (or override built-ins), add a key:value pair where the key is
        #: what the user should enter (prefixed with :attr:`magic_prefix`)
        #: and the value is a callable that will be called when the argument
        #: is entered. If you would like to display a message in Alfred, the
        #: function should return a ``unicode`` string.
        #:
        #: By default, the magic arguments documented
        #: :ref:`here <magic-arguments>` are registered.
        self.magic_arguments = {}

        self._register_default_magic()

        if libraries:
            sys.path = libraries + sys.path

        hooks.workflow_initialized.send(self)

    ####################################################################
    # API methods
    ####################################################################

    # info.plist contents and alfred_* environment variables  ----------

    @property
    def alfred_env(self):
        """Alfred's environmental variables minus the ``alfred_`` prefix.

        .. versionadded:: 1.7

        The variables Alfred 2.4+ exports are:

        ============================  =========================================
        Variable                      Description
        ============================  =========================================
        alfred_preferences            Path to Alfred.alfredpreferences
                                      (where your workflows and settings are
                                      stored).
        alfred_preferences_localhash  Machine-specific preferences are stored
                                      in ``Alfred.alfredpreferences/preferences/local/<hash>``
                                      (see ``alfred_preferences`` above for
                                      the path to ``Alfred.alfredpreferences``)
        alfred_theme                  ID of selected theme
        alfred_theme_background       Background colour of selected theme in
                                      format ``rgba(r,g,b,a)``
        alfred_theme_subtext          Show result subtext.
                                      ``0`` = Always,
                                      ``1`` = Alternative actions only,
                                      ``2`` = Selected result only,
                                      ``3`` = Never
        alfred_version                Alfred version number, e.g. ``'2.4'``
        alfred_version_build          Alfred build number, e.g. ``277``
        alfred_workflow_bundleid      Bundle ID, e.g.
                                      ``net.deanishe.alfred-mailto``
        alfred_workflow_cache         Path to workflow's cache directory
        alfred_workflow_data          Path to workflow's data directory
        alfred_workflow_name          Name of current workflow
        alfred_workflow_uid           UID of workflow
        ============================  =========================================

        **Note:** all values are Unicode strings except ``version_build`` and
        ``theme_subtext``, which are integers.

        :returns: ``dict`` of Alfred's environmental variables without the
            ``alfred_`` prefix, e.g. ``preferences``, ``workflow_data``.

        """

        if self._alfred_env is not None:
            return self._alfred_env

        data = {}

        for key in (
                'alfred_preferences',
                'alfred_preferences_localhash',
                'alfred_theme',
                'alfred_theme_background',
                'alfred_theme_subtext',
                'alfred_version',
                'alfred_version_build',
                'alfred_workflow_bundleid',
                'alfred_workflow_cache',
                'alfred_workflow_data',
                'alfred_workflow_name',
                'alfred_workflow_uid'):

            value = os.getenv(key)

            if isinstance(value, str):
                if key in ('alfred_version_build', 'alfred_theme_subtext'):
                    value = int(value)
                else:
                    value = self.decode(value)

            data[key[7:]] = value

        self._alfred_env = data

        return self._alfred_env

    @property
    def info(self):
        """:class:`dict` of ``info.plist`` contents."""

        return env.info

    @property
    def bundleid(self):
        """Workflow bundle ID from environmental vars or ``info.plist``.

        :returns: bundle ID
        :rtype: ``unicode``

        """

        return env.bundleid

    @property
    def name(self):
        """Workflow name from Alfred's environmental vars or ``info.plist``.

        :returns: workflow name
        :rtype: ``unicode``

        """

        return env.name

    @property
    def version(self):
        """Return the version of the workflow

        .. versionadded:: 1.9.10

        Get the version from the ``update_settings`` dict passed on
        instantiation or the ``version`` file located in the workflow's
        root directory. Return ``None`` if neither exist or
        :class:`ValueError` if the version number is invalid (i.e. not
        semantic).

        :returns: Version of the workflow (not Alfred-Workflow)
        :rtype: :class:`~workflow.update.Version` object

        """
        # TODO: version file only! (?)
        if self._version is base.UNSET:

            version = None
            # First check `update_settings`
            if self._update_settings:
                version = self._update_settings.get('version')

            # Fallback to `version` file
            if not version:
                version = env['version']
                log.debug('version from env : {0}'.format(version))

            if version and isinstance(version, basestring):
                version = base.Version(version)

            self._version = version

        return self._version

    # Workflow utility methods -----------------------------------------

    @property
    def args(self):
        """Return command line args as normalised unicode.

        Args are decoded and normalised via :meth:`~Workflow.decode`.

        The encoding and normalisation are the ``input_encoding`` and
        ``normalization`` arguments passed to :class:`Workflow` (``UTF-8``
        and ``NFC`` are the defaults).

        If :class:`Workflow` is called with ``capture_args=True``
        (the default), :class:`Workflow` will look for certain
        ``workflow:*`` args and, if found, perform the corresponding
        actions and exit the workflow.

        See :ref:`Magic arguments <magic-arguments>` for details.

        """
        # TODO: Extract into plugins

        msg = None
        args = [self.decode(arg) for arg in sys.argv[1:]]

        # Handle magic args
        if len(args) and self._capture_args:
            for name in self.magic_arguments:
                key = '{0}{1}'.format(self.magic_prefix, name)
                if key in args:
                    msg = self.magic_arguments[name]()

            if msg:
                log.debug(msg)
                if not sys.stdout.isatty():  # Show message in Alfred
                    self.add_item(msg, valid=False, icon=icons.INFO)
                    self.send_feedback()
                sys.exit(0)
        return args

    @property
    def cachedir(self):
        """Path to workflow's cache directory.

        The cache directory is a subdirectory of Alfred's own cache directory in
        ``~/Library/Caches``. The full path is:

        ``~/Library/Caches/com.runningwithcrayons.Alfred-2/Workflow Data/<bundle id>``

        :returns: full path to workflow's cache directory
        :rtype: ``unicode``

        """

        return util.create_directory(env.cachedir)

    @property
    def datadir(self):
        """Path to workflow's data directory.

        The data directory is a subdirectory of Alfred's own data directory in
        ``~/Library/Application Support``. The full path is:

        ``~/Library/Application Support/Alfred 2/Workflow Data/<bundle id>``

        :returns: full path to workflow data directory
        :rtype: ``unicode``

        """

        return util.create_directory(env.datadir)

    @property
    def workflowdir(self):
        """Path to workflow's root directory (where ``info.plist`` is).

        :returns: full path to workflow root directory
        :rtype: ``unicode``

        """

        return util.create_directory(env.workflowdir)

    def cachefile(self, filename):
        """Return full path to ``filename`` within your workflow's
        :attr:`cache directory <Workflow.cachedir>`.

        :param filename: basename of file
        :type filename: ``unicode``
        :returns: full path to file within cache directory
        :rtype: ``unicode``

        """

        return os.path.join(self.cachedir, filename)

    def datafile(self, filename):
        """Return full path to ``filename`` within your workflow's
        :attr:`data directory <Workflow.datadir>`.

        :param filename: basename of file
        :type filename: ``unicode``
        :returns: full path to file within data directory
        :rtype: ``unicode``

        """

        return os.path.join(self.datadir, filename)

    def workflowfile(self, filename):
        """Return full path to ``filename`` in workflow's root dir
        (where ``info.plist`` is).

        :param filename: basename of file
        :type filename: ``unicode``
        :returns: full path to file within data directory
        :rtype: ``unicode``

        """

        return os.path.join(self.workflowdir, filename)

    @property
    def logfile(self):
        """Return path to logfile

        :returns: path to logfile within workflow's cache directory
        :rtype: ``unicode``

        """

        return self.cachefile('{0}.log'.format(self.bundleid))

    @property
    def logger(self):
        """Create and return a logger that logs to both console and
        a log file.

        Use :meth:`open_log` to open the log file in Console.

        :returns: an initialised :class:`~logging.Logger`

        """

        if self._logger:
            return self._logger

        # Initialise new logger and optionally handlers

        base.init_logging(logfile=self.logfile, level=logging.INFO)
        self._logger = base.get_logger('')

        return self._logger

        # if not len(logger.handlers):  # Only add one set of handlers
        #     logfile = logging.handlers.RotatingFileHandler(
        #         self.logfile,
        #         maxBytes=1024*1024,
        #         backupCount=0)

        #     console = logging.StreamHandler()

        #     fmt = logging.Formatter(
        #         '%(asctime)s %(filename)s:%(lineno)s'
        #         ' %(levelname)-8s %(message)s',
        #         datefmt='%H:%M:%S')

        #     logfile.setFormatter(fmt)
        #     console.setFormatter(fmt)

        #     logger.addHandler(logfile)
        #     logger.addHandler(console)

        # logger.setLevel(logging.DEBUG)
        # self._logger = logger

        # return self._logger

    @logger.setter
    def logger(self, logger):
        """Set a custom logger.

        :param logger: The logger to use
        :type logger: `~logging.Logger` instance

        """

        self._logger = logger

    @property
    def settings_path(self):
        """Path to settings file within workflow's data directory.

        :returns: path to ``settings.json`` file
        :rtype: ``unicode``

        """

        if not self._settings_path:
            self._settings_path = self.datafile('settings.json')
        return self._settings_path

    @property
    def settings(self):
        """Return a dictionary subclass that saves itself when changed.

        See :ref:`manual-settings` in the :ref:`user-manual` for more
        information on how to use :attr:`settings` and **important
        limitations** on what it can do.

        :returns: :class:`~workflow.storage.PersistentDict` instance
            initialised from the data in JSON file at
            :attr:`settings_path` or if that doesn't exist, with the
            ``default_settings`` :class:`dict` passed to
            :class:`Workflow` on instantiation.
        :rtype: :class:`~workflow.storage.PersistentDict` instance

        """

        if not self._settings:
            log.debug('Reading settings from `{0}` ...'.format(
                      self.settings_path))
            self._settings = storage.PersistentDict(self.settings_path,
                                                    self._default_settings)
        return self._settings

    @property
    def cache_serializer(self):
        """Name of default cache serializer.

        .. versionadded:: 1.8

        This serializer is used by :meth:`cache_data()` and
        :meth:`cached_data()`

        See :class:`SerializerManager` for details.

        :returns: serializer name
        :rtype: ``unicode``

        """

        return self._cache_serializer

    @cache_serializer.setter
    def cache_serializer(self, serializer_name):
        """Set the default cache serialization format.

        .. versionadded:: 1.8

        This serializer is used by :meth:`cache_data()` and
        :meth:`cached_data()`

        The specified serializer must already by registered with the
        :class:`SerializerManager` at `~workflow.workflow.manager`,
        otherwise a :class:`ValueError` will be raised.

        :param serializer_name: Name of default serializer to use.
        :type serializer_name:

        """

        if manager.serializer(serializer_name) is None:
            raise ValueError(
                'Unknown serializer : `{0}`. Register your serializer '
                'with `manager` first.'.format(serializer_name))

        log.debug(
            'default cache serializer set to `{0}`'.format(serializer_name))

        self._cache_serializer = serializer_name

    @property
    def data_serializer(self):
        """Name of default data serializer.

        .. versionadded:: 1.8

        This serializer is used by :meth:`store_data()` and
        :meth:`stored_data()`

        See :class:`SerializerManager` for details.

        :returns: serializer name
        :rtype: ``unicode``

        """

        return self._data_serializer

    @data_serializer.setter
    def data_serializer(self, serializer_name):
        """Set the default cache serialization format.

        .. versionadded:: 1.8

        This serializer is used by :meth:`store_data()` and
        :meth:`stored_data()`

        The specified serializer must already by registered with the
        :class:`SerializerManager` at `~workflow.workflow.manager`,
        otherwise a :class:`ValueError` will be raised.

        :param serializer_name: Name of serializer to use by default.

        """

        if manager.serializer(serializer_name) is None:
            raise ValueError(
                'Unknown serializer : `{0}`. Register your serializer '
                'with `manager` first.'.format(serializer_name))

        log.debug(
            'default data serializer set to `{0}`'.format(serializer_name))

        self._data_serializer = serializer_name

    def stored_data(self, name):
        """Retrieve data from data directory. Returns ``None`` if there
        are no data stored.

        .. versionadded:: 1.8

        :param name: name of datastore

        """

        metadata_path = self.datafile('.{0}.alfred-workflow'.format(name))

        if not os.path.exists(metadata_path):
            log.debug('No data stored for `{0}`'.format(name))
            return None

        with open(metadata_path, 'rb') as file_obj:
            serializer_name = file_obj.read().strip()

        serializer = manager.serializer(serializer_name)

        if serializer is None:
            raise ValueError(
                'Unknown serializer `{0}`. Register a corresponding serializer'
                ' with `manager.register()` to load this data.'.format(
                    serializer_name))

        log.debug('Data `{0}` stored in `{1}` format'.format(
            name, serializer_name))

        filename = '{0}.{1}'.format(name, serializer_name)
        data_path = self.datafile(filename)

        if not os.path.exists(data_path):
            log.debug('No data stored for `{0}`'.format(name))
            if os.path.exists(metadata_path):
                os.unlink(metadata_path)

            return None

        with open(data_path, 'rb') as file_obj:
            data = serializer.load(file_obj)

        log.debug('Stored data loaded from : {0}'.format(data_path))

        return data

    def store_data(self, name, data, serializer=None):
        """Save data to data directory.

        .. versionadded:: 1.8

        If ``data`` is ``None``, the datastore will be deleted.

        :param name: name of datastore
        :param data: object(s) to store. **Note:** some serializers
            can only handled certain types of data.
        :param serializer: name of serializer to use. If no serializer
            is specified, the default will be used. See
            :class:`SerializerManager` for more information.
        :returns: data in datastore or ``None``

        """

        serializer_name = serializer or self.data_serializer

        # In order for `stored_data()` to be able to load data stored with
        # an arbitrary serializer, yet still have meaningful file extensions,
        # the format (i.e. extension) is saved to an accompanying file
        metadata_path = self.datafile('.{0}.alfred-workflow'.format(name))
        filename = '{0}.{1}'.format(name, serializer_name)
        data_path = self.datafile(filename)

        if data_path == self.settings_path:
            raise ValueError(
                'Cannot save data to' +
                '`{0}` with format `{1}`. '.format(name, serializer_name) +
                "This would overwrite Alfred-Workflow's settings file.")

        serializer = manager.serializer(serializer_name)

        if serializer is None:
            raise ValueError(
                'Invalid serializer `{0}`. Register your serializer with '
                '`manager.register()` first.'.format(serializer_name))

        if data is None:  # Delete cached data
            for path in (metadata_path, data_path):
                if os.path.exists(path):
                    os.unlink(path)
                    log.debug('Deleted data file : {0}'.format(path))

            return

        # Save file extension
        with open(metadata_path, 'wb') as file_obj:
            file_obj.write(serializer_name)

        with open(data_path, 'wb') as file_obj:
            serializer.dump(data, file_obj)

        log.debug('Stored data saved at : {0}'.format(data_path))

    def cached_data(self, name, data_func=None, max_age=60):
        """Retrieve data from cache or re-generate and re-cache data if
        stale/non-existant. If ``max_age`` is 0, return cached data no
        matter how old.

        :param name: name of datastore
        :param data_func: function to (re-)generate data.
        :type data_func: ``callable``
        :param max_age: maximum age of cached data in seconds
        :type max_age: ``int``
        :returns: cached data, return value of ``data_func`` or ``None``
            if ``data_func`` is not set

        """

        serializer = manager.serializer(self.cache_serializer)

        cache_path = self.cachefile('%s.%s' % (name, self.cache_serializer))
        age = self.cached_data_age(name)

        if (age < max_age or max_age == 0) and os.path.exists(cache_path):

            with open(cache_path, 'rb') as file_obj:
                log.debug('Loading cached data from : %s',
                                  cache_path)
                return serializer.load(file_obj)

        if not data_func:
            return None

        data = data_func()
        self.cache_data(name, data)

        return data

    def cache_data(self, name, data):
        """Save ``data`` to cache under ``name``.

        If ``data`` is ``None``, the corresponding cache file will be
        deleted.

        :param name: name of datastore
        :param data: data to store. This may be any object supported by
                the cache serializer

        """

        serializer = manager.serializer(self.cache_serializer)

        cache_path = self.cachefile('%s.%s' % (name, self.cache_serializer))

        if data is None:
            if os.path.exists(cache_path):
                os.unlink(cache_path)
                log.debug('Deleted cache file : %s', cache_path)
            return

        with open(cache_path, 'wb') as file_obj:
            serializer.dump(data, file_obj)

        log.debug('Cached data saved at : %s', cache_path)

    def cached_data_fresh(self, name, max_age):
        """Is data cached at `name` less than `max_age` old?

        :param name: name of datastore
        :param max_age: maximum age of data in seconds
        :type max_age: ``int``
        :returns: ``True`` if data is less than ``max_age`` old, else
            ``False``

        """

        age = self.cached_data_age(name)

        if not age:
            return False

        return age < max_age

    def cached_data_age(self, name):
        """Return age of data cached at `name` in seconds or 0 if
        cache doesn't exist

        :param name: name of datastore
        :type name: ``unicode``
        :returns: age of datastore in seconds
        :rtype: ``int``

        """

        cache_path = self.cachefile('%s.%s' % (name, self.cache_serializer))

        if not os.path.exists(cache_path):
            return 0

        return time.time() - os.stat(cache_path).st_mtime

    def filter(self, query, items, key=lambda x: x, ascending=False,
               include_score=False, min_score=0, max_results=0,
               match_on=search.MATCH_ALL, fold_diacritics=True):
        """Fuzzy search filter. Returns list of ``items`` that match ``query``.

        ``query`` is case-insensitive. Any item that does not contain the
        entirety of ``query`` is rejected.

        See :func:`workflow.search.filter` for detailed documentation.

        """

        fold_diacritics = self.settings.get(base.KEY_DIACRITICS,
                                            fold_diacritics)

        return search.filter(query, items, key, ascending, include_score,
                             min_score, max_results, match_on, fold_diacritics)

    def run(self, func):
        """Call ``func`` to run your workflow

        :param func: Callable to call with ``self`` (i.e. the :class:`Workflow`
            instance) as first argument.

        ``func`` will be called with :class:`Workflow` instance as first
        argument.

        ``func`` should be the main entry point to your workflow.

        Any exceptions raised will be logged and an error message will be
        output to Alfred.

        """

        start = time.time()

        # Call workflow's entry function/method within a try-except block
        # to catch any errors and display an error message in Alfred
        try:
            if self.version:
                log.debug('Workflow version : {0}'.format(
                                  self.version))

            # Run update check if configured for self-updates.
            # This call has to go in the `run` try-except block, as it will
            # initialise `self.settings`, which will raise an exception
            # if `settings.json` isn't valid.
            if self._update_settings:
                self.check_update()

            hooks.workflow_will_run.send(self)

            # Run workflow's entry function/method
            func(self)

            hooks.workflow_did_run.send(self)
            # Set last version run to current version after a successful
            # run
            self.set_last_version()

        except Exception as err:
            log.exception(err)
            self.exception = err
            hooks.workflow_error.send(self)
            if self.help_url:
                log.info(
                    'For assistance, see: {0}'.format(self.help_url))
            if not sys.stdout.isatty():
                # Show error in Alfred
                self._items = []
                if self._name:  # pragma: no cover
                    name = self._name
                elif self._bundleid:  # pragma: no cover
                    name = self._bundleid
                else:
                    name = os.path.dirname(__file__)
                self.add_item("Error in workflow '%s'" % name, unicode(err),
                              icon=icons.ERROR)
                self.send_feedback()
            return 1
        finally:
            log.debug('Workflow finished in {0:0.3f} seconds.'.format(
                      time.time() - start))
        return 0

    # Alfred feedback methods ------------------------------------------

    def add_item(self, title, subtitle='', modifier_subtitles=None, arg=None,
                 autocomplete=None, valid=False, uid=None, icon=None,
                 icontype=None, type=None, largetext=None, copytext=None):
        """Add an item to be output to Alfred

        Passes arguments through to :class:`XMLGenerator` method
        :meth:`~workflow.feedback.XMLGenerator.add_item`.

        """

        return self._xmlgen.add_item(title, subtitle, modifier_subtitles, arg,
                                     autocomplete, valid, uid, icon, icontype,
                                     type, largetext, copytext)

    def send_feedback(self):
        """Print stored items to console/Alfred as XML."""
        output = self._xmlgen.xml()
        receiver, result = hooks.xml_generator_done.first_response(output)
        if receiver is not None:  # A plugin returned something
            log.debug('hook result : {0} from {1}'.format(result,
                                                          receiver.__name__))
            if isinstance(result, str):
                output = result
            else:
                raise ValueError(
                    'Plugin returned `{0}`, not `str` : {1!r}'.format(
                        result.__class__.__name__, result))
        else:
            log.debug('No hooks for XML generator')
        sys.stdout.write(output)
        sys.stdout.flush()

    ####################################################################
    # Updating methods
    ####################################################################

    @property
    def first_run(self):
        """Return ``True`` if it's the first time this version has run.

        .. versionadded:: 1.9.10

        Raises a :class:`ValueError` if :attr:`version` isn't set.

        """

        if not self.version:
            raise ValueError('No workflow version set')

        if not self.last_version_run:
            return True

        return self.version != self.last_version_run

    @property
    def last_version_run(self):
        """Return version of last version to run (or ``None``)

        .. versionadded:: 1.9.10

        :returns: :class:`~workflow.update.Version` instance
            or ``None``

        """

        if self._last_version_run is base.UNSET:

            version = self.settings.get(base.KEY_VERSION_LAST_RUN)
            if version:
                version = base.Version(version)

            self._last_version_run = version

        log.debug('Last run version : {0}'.format(
                  self._last_version_run))

        return self._last_version_run

    def set_last_version(self, version=None):
        """Set :attr:`last_version_run` to current version

        .. versionadded:: 1.9.10

        :param version: version to store (default is current version)
        :type version: :class:`~workflow.update.Version` instance
            or ``unicode``
        :returns: ``True`` if version is saved, else ``False``

        """

        if not version:
            if not self.version:
                log.warning(
                    "Can't save last version: workflow has no version")
                return False

            version = self.version

        if isinstance(version, basestring):
            version = base.Version(version)

        self.settings[base.KEY_VERSION_LAST_RUN] = str(version)

        log.debug('Set last run version : {0}'.format(version))

        return True

    @property
    def update_available(self):
        """Is an update available?

        .. versionadded:: 1.9

        See :ref:`manual-updates` in the :ref:`user-manual` for detailed
        information on how to enable your workflow to update itself.

        :returns: ``True`` if an update is available, else ``False``

        """

        update_data = self.cached_data(base.KEY_UPDATE_DATA, max_age=0)
        log.debug('update_data : {0}'.format(update_data))

        if not update_data or not update_data.get('available'):
            return False

        return update_data['available']

    def check_update(self, force=False):
        """Call update script if it's time to check for a new release

        .. versionadded:: 1.9

        The update script will be run in the background, so it won't
        interfere in the execution of your workflow.

        See :ref:`manual-updates` in the :ref:`user-manual` for detailed
        information on how to enable your workflow to update itself.

        :param force: Force update check
        :type force: ``Boolean``

        """

        frequency = self._update_settings.get('frequency',
                                              DEFAULT_UPDATE_FREQUENCY)

        if not force and not self.settings.get(base.KEY_AUTO_UPDATE, True):
            log.debug('Auto update turned off by user')
            return

        # Check for new version if it's time
        if (force or not self.cached_data_fresh(
                base.KEY_UPDATE_DATA, frequency * 86400)):

            github_slug = self._update_settings['github_slug']
            # version = self._update_settings['version']
            version = str(self.version)

            from workflow.background import run_in_background

            # update.py is adjacent to this file
            update_script = os.path.join(os.path.dirname(__file__),
                                         b'update.py')

            cmd = ['/usr/bin/python', update_script, 'check', github_slug,
                   version]

            log.info('Checking for update ...')

            run_in_background(base.KEY_UPDATER, cmd)

        else:
            log.debug('Update check not due')

    def start_update(self):
        """Check for update and download and install new workflow file

        .. versionadded:: 1.9

        See :ref:`manual-updates` in the :ref:`user-manual` for detailed
        information on how to enable your workflow to update itself.

        :returns: ``True`` if an update is available and will be
            installed, else ``False``

        """

        from workflow import update

        github_slug = self._update_settings['github_slug']
        # version = self._update_settings['version']
        version = str(self.version)

        if not update.check_update(github_slug, version):
            return False

        from workflow.background import run_in_background

        # update.py is adjacent to this file
        update_script = os.path.join(os.path.dirname(__file__),
                                     b'update.py')

        cmd = ['/usr/bin/python', update_script, 'install', github_slug,
               version]

        log.debug('Downloading update ...')
        run_in_background(base.KEY_INSTALLER, cmd)

        return True

    ####################################################################
    # Keychain password storage methods
    ####################################################################

    def save_password(self, account, password, service=None):
        """Save account credentials.

        If the account exists, the old password will first be deleted
        (Keychain throws an error otherwise).

        If something goes wrong, a :class:`base.KeychainError` exception
        will be raised.

        :param account: name of the account the password is for, e.g.
            "Pinboard"
        :type account: ``unicode``
        :param password: the password to secure
        :type password: ``unicode``
        :param service: Name of the service. By default, this is the
            workflow's bundle ID
        :type service: ``unicode``

        """
        if not service:
            service = self.bundleid

        try:
            self._call_security('add-generic-password', service, account,
                                '-w', password)
            log.debug('Saved password : %s:%s', service, account)

        except base.PasswordExists:
            log.debug('Password exists : %s:%s', service, account)
            current_password = self.get_password(account, service)

            if current_password == password:
                log.debug('Password unchanged')

            else:
                self.delete_password(account, service)
                self._call_security('add-generic-password', service,
                                    account, '-w', password)
                log.debug('save_password : %s:%s', service, account)

    def get_password(self, account, service=None):
        """Retrieve the password saved at ``service/account``. Raise
        :class:`PasswordNotFound` exception if password doesn't exist.

        :param account: name of the account the password is for, e.g.
            "Pinboard"
        :type account: ``unicode``
        :param service: Name of the service. By default, this is the workflow's
                        bundle ID
        :type service: ``unicode``
        :returns: account password
        :rtype: ``unicode``

        """

        if not service:
            service = self.bundleid

        output = self._call_security('find-generic-password', service,
                                     account, '-g')

        # Parsing of `security` output is adapted from python-keyring
        # by Jason R. Coombs
        # https://pypi.python.org/pypi/keyring
        m = re.search(
            r'password:\s*(?:0x(?P<hex>[0-9A-F]+)\s*)?(?:"(?P<pw>.*)")?',
            output)

        if m:
            groups = m.groupdict()
            h = groups.get('hex')
            password = groups.get('pw')
            if h:
                password = unicode(binascii.unhexlify(h), 'utf-8')

        log.debug('Got password : %s:%s', service, account)

        return password

    def delete_password(self, account, service=None):
        """Delete the password stored at ``service/account``. Raises
        :class:`PasswordNotFound` if account is unknown.

        :param account: name of the account the password is for, e.g.
            "Pinboard"
        :type account: ``unicode``
        :param service: Name of the service. By default, this is the workflow's
                        bundle ID
        :type service: ``unicode``

        """

        if not service:
            service = self.bundleid

        self._call_security('delete-generic-password', service, account)

        log.debug('Deleted password : %s:%s', service, account)

    ####################################################################
    # Methods for workflow:* magic args
    ####################################################################

    def _register_default_magic(self):
        """Register the built-in magic arguments"""
        # TODO: refactor & simplify

        # Wrap callback and message with callable
        def callback(func, msg):
            def wrapper():
                func()
                return msg

            return wrapper

        self.magic_arguments['delcache'] = callback(self.clear_cache,
                                                    'Deleted workflow cache')
        self.magic_arguments['deldata'] = callback(self.clear_data,
                                                   'Deleted workflow data')
        self.magic_arguments['delsettings'] = callback(
            self.clear_settings, 'Deleted workflow settings')
        self.magic_arguments['reset'] = callback(self.reset,
                                                 'Reset workflow')
        self.magic_arguments['openlog'] = callback(self.open_log,
                                                   'Opening workflow log file')
        self.magic_arguments['opencache'] = callback(
            self.open_cachedir, 'Opening workflow cache directory')
        self.magic_arguments['opendata'] = callback(
            self.open_datadir, 'Opening workflow data directory')
        self.magic_arguments['openworkflow'] = callback(
            self.open_workflowdir, 'Opening workflow directory')
        self.magic_arguments['openterm'] = callback(
            self.open_terminal, 'Opening workflow root directory in Terminal')

        # Diacritic folding
        def fold_on():
            self.settings[base.KEY_DIACRITICS] = True
            return 'Diacritics will always be folded'

        def fold_off():
            self.settings[base.KEY_DIACRITICS] = False
            return 'Diacritics will never be folded'

        def fold_default():
            if base.KEY_DIACRITICS in self.settings:
                del self.settings[base.KEY_DIACRITICS]
            return 'Diacritics folding reset'

        self.magic_arguments['foldingon'] = fold_on
        self.magic_arguments['foldingoff'] = fold_off
        self.magic_arguments['foldingdefault'] = fold_default

        # Updates
        def update_on():
            self.settings[base.KEY_AUTO_UPDATE] = True
            return 'Auto update turned on'

        def update_off():
            self.settings[base.KEY_AUTO_UPDATE] = False
            return 'Auto update turned off'

        def do_update():
            if self.start_update():
                return 'Downloading and installing update ...'
            else:
                return 'No update available'

        self.magic_arguments['autoupdate'] = update_on
        self.magic_arguments['noautoupdate'] = update_off
        self.magic_arguments['update'] = do_update

        # Help
        def do_help():
            if self.help_url:
                self.open_help()
                return 'Opening workflow help URL in browser'
            else:
                return 'Workflow has no help URL'

        def show_version():
            if self.version:
                return 'Version: {0}'.format(self.version)
            else:
                return 'This workflow has no version number'

        def list_magic():
            """Display all available magic args in Alfred"""
            isatty = sys.stderr.isatty()
            for name in sorted(self.magic_arguments.keys()):
                if name == 'magic':
                    continue
                arg = '{0}{1}'.format(self.magic_prefix, name)
                log.debug(arg)

                if not isatty:
                    self.add_item(arg, icon=icons.INFO)

            if not isatty:
                self.send_feedback()

        self.magic_arguments['help'] = do_help
        self.magic_arguments['magic'] = list_magic
        self.magic_arguments['version'] = show_version

    def clear_cache(self, filter_func=lambda f: True):
        """Delete all files in workflow's :attr:`cachedir`.

        :param filter_func: Callable to determine whether a file should be
            deleted or not. ``filter_func`` is called with the filename
            of each file in the data directory. If it returns ``True``,
            the file will be deleted.
            By default, *all* files will be deleted.
        :type filter_func: ``callable``
        """
        self._delete_directory_contents(self.cachedir, filter_func)

    def clear_data(self, filter_func=lambda f: True):
        """Delete all files in workflow's :attr:`datadir`.

        :param filter_func: Callable to determine whether a file should be
            deleted or not. ``filter_func`` is called with the filename
            of each file in the data directory. If it returns ``True``,
            the file will be deleted.
            By default, *all* files will be deleted.
        :type filter_func: ``callable``
        """
        self._delete_directory_contents(self.datadir, filter_func)

    def clear_settings(self):
        """Delete workflow's :attr:`settings_path`."""
        if os.path.exists(self.settings_path):
            os.unlink(self.settings_path)
            log.debug('Deleted : %r', self.settings_path)

    def reset(self):
        """Delete :attr:`settings <settings_path>`, :attr:`cache <cachedir>`
        and :attr:`data <datadir>`

        """

        self.clear_cache()
        self.clear_data()
        self.clear_settings()

    def open_log(self):
        """Open workflows :attr:`logfile` in standard
        application (usually Console.app).

        """

        subprocess.call(['open', self.logfile])

    def open_cachedir(self):
        """Open the workflow's :attr:`cachedir` in Finder."""
        subprocess.call(['open', self.cachedir])

    def open_datadir(self):
        """Open the workflow's :attr:`datadir` in Finder."""
        subprocess.call(['open', self.datadir])

    def open_workflowdir(self):
        """Open the workflow's :attr:`workflowdir` in Finder."""
        subprocess.call(['open', self.workflowdir])

    def open_terminal(self):
        """Open a Terminal window at workflow's :attr:`workflowdir`."""

        subprocess.call(['open', '-a', 'Terminal',
                        self.workflowdir])

    def open_help(self):
        """Open :attr:`help_url` in default browser"""
        subprocess.call(['open', self.help_url])

        return 'Opening workflow help URL in browser'

    ####################################################################
    # Helper methods
    ####################################################################

    def decode(self, text, encoding=None, normalization=None):
        """Return ``text`` as normalised unicode.

        If ``encoding`` and/or ``normalization`` is ``None``, the
        ``input_encoding``and ``normalization`` parameters passed to
        :class:`Workflow` are used.

        :param text: string
        :type text: encoded or Unicode string. If ``text`` is already a
            Unicode string, it will only be normalised.
        :param encoding: The text encoding to use to decode ``text`` to
            Unicode.
        :type encoding: ``unicode`` or ``None``
        :param normalization: The nomalisation form to apply to ``text``.
        :type normalization: ``unicode`` or ``None``
        :returns: decoded and normalised ``unicode``

        """

        encoding = encoding or self._input_encoding
        normalization = normalization or self._normalizsation
        return util.decode(text, encoding, normalization)

    def fold_to_ascii(self, text):
        """Convert non-ASCII characters to closest ASCII equivalent.

        .. versionadded:: 1.3

        .. note:: This only works for a subset of European languages.

        :param text: text to convert
        :type text: ``unicode``
        :returns: text containing only ASCII characters
        :rtype: ``unicode``

        """
        return search.fold_to_ascii(text)

    def _delete_directory_contents(self, dirpath, filter_func):
        """Delete all files in a directory

        :param dirpath: path to directory to clear
        :type dirpath: ``unicode`` or ``str``
        :param filter_func function to determine whether a file shall be
            deleted or not.
        :type filter_func ``callable``
        """

        if os.path.exists(dirpath):
            for filename in os.listdir(dirpath):
                if not filter_func(filename):
                    continue
                path = os.path.join(dirpath, filename)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                log.debug('Deleted : %r', path)

    def _call_security(self, action, service, account, *args):
        """Call the ``security`` CLI app that provides access to keychains.


        May raise `PasswordNotFound`, `PasswordExists` or `KeychainError`
        exceptions (the first two are subclasses of `KeychainError`).

        :param action: The ``security`` action to call, e.g.
                           ``add-generic-password``
        :type action: ``unicode``
        :param service: Name of the service.
        :type service: ``unicode``
        :param account: name of the account the password is for, e.g.
            "Pinboard"
        :type account: ``unicode``
        :param password: the password to secure
        :type password: ``unicode``
        :param *args: list of command line arguments to be passed to
                      ``security``
        :type *args: `list` or `tuple`
        :returns: ``(retcode, output)``. ``retcode`` is an `int`, ``output`` a
                  ``unicode`` string.
        :rtype: `tuple` (`int`, ``unicode``)

        """

        cmd = ['security', action, '-s', service, '-a', account] + list(args)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        retcode, output = p.wait(), p.stdout.read().strip().decode('utf-8')
        if retcode == 44:  # password does not exist
            raise base.PasswordNotFound()
        elif retcode == 45:  # password already exists
            raise base.PasswordExists()
        elif retcode > 0:
            err = base.KeychainError('Unknown Keychain error : %s' % output)
            err.retcode = retcode
            raise err
        return output
