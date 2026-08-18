import socket
import threading
import time
from functools import wraps

from adbutils.errors import AdbError

from module.base.decorator import cached_property, del_cached_property, has_cached_property
from module.base.timer import Timer
from module.base.utils import *
from module.device.connection import Connection
from module.device.managed_screenshot_crop import managed_screenshot_crop_from_environment
from module.device.method.minitouch import CommandBuilder, insert_swipe
from module.device.method.utils import RETRY_TRIES, handle_adb_error, handle_unknown_host_service, retry_sleep
from module.exception import RequestHumanTakeover, ScriptError
from module.logger import logger


def retry(func):
    @wraps(func)
    def retry_wrapper(self, *args, **kwargs):
        """
        Args:
            self (MaaTouch):
        """
        init = None
        for _ in range(RETRY_TRIES):
            try:
                if callable(init):
                    time.sleep(retry_sleep(_))
                    init()
                return func(self, *args, **kwargs)
            # Can't handle
            except RequestHumanTakeover:
                break
            # When adb server was killed
            except ConnectionResetError as e:
                logger.error(e)

                def init():
                    self.adb_reconnect()
                    del_cached_property(self, '_maatouch_builder')
            # Emulator closed
            except ConnectionAbortedError as e:
                logger.error(e)

                def init():
                    self.adb_reconnect()
                    del_cached_property(self, '_maatouch_builder')
            # AdbError
            except AdbError as e:
                if handle_adb_error(e):
                    def init():
                        self.adb_reconnect()
                        del_cached_property(self, '_maatouch_builder')
                elif handle_unknown_host_service(e):
                    def init():
                        self.adb_start_server()
                        self.adb_reconnect()
                        del_cached_property(self, '_maatouch_builder')
                else:
                    break
            # MaaTouchNotInstalledError: Received "Aborted" from MaaTouch
            except MaaTouchNotInstalledError as e:
                logger.error(e)

                def init():
                    self.maatouch_install()
                    del_cached_property(self, '_maatouch_builder')
            except BrokenPipeError as e:
                logger.error(e)

                def init():
                    del_cached_property(self, '_maatouch_builder')
            # Unknown, probably a trucked image
            except Exception as e:
                logger.exception(e)

                def init():
                    pass

        logger.critical(f'Retry {func.__name__}() failed')
        raise RequestHumanTakeover

    return retry_wrapper


class MaatouchBuilder(CommandBuilder):
    def __init__(self, device, contact=0, handle_orientation=False):
        """
        Args:
            device (MaaTouch):
        """

        super().__init__(device, contact, handle_orientation)

    def send(self):
        return self.device.maatouch_send(builder=self)


class MaaTouchNotInstalledError(Exception):
    pass


def _assert_managed_crop_landscape_frame_fresh(device):
    if managed_screenshot_crop_from_environment() is None:
        return
    captured_at = getattr(device, '_managed_crop_landscape_frame_at', None)
    if captured_at is None or time.monotonic() - captured_at > 15:
        raise ScriptError(
            'Managed phone MaaTouch send requires a recent landscape screenshot'
        )


def _maatouch_payload_operations(builder, content):
    commands = getattr(builder, 'commands', None)
    if commands is not None:
        return [
            (
                getattr(command, 'operation', None),
                getattr(command, 'contact', None),
            )
            for command in commands
        ]

    operations = []
    for row in content.splitlines():
        row = row.strip()
        if not row:
            continue
        parts = row.split()
        operation = parts[0]
        contact = None
        if operation in {'d', 'm', 'u'} and len(parts) > 1:
            try:
                contact = int(parts[1])
            except ValueError:
                pass
        operations.append((operation, contact))
    return operations


def _maatouch_payload_requires_landscape_frame(builder, content):
    """Return whether a wire payload can create or move a touch contact."""
    cleanup_operations = {'u', 'r', 'c', 'w'}
    command_operations = _maatouch_payload_operations(builder, content)
    operations = [operation for operation, _contact in command_operations]
    if not operations:
        return False
    if any(operation not in cleanup_operations for operation in operations):
        return True

    # A standalone commit may publish a down/move that the server buffered
    # from an earlier packet. Stale bypass therefore requires an actual
    # release/reset followed by a commit; c/w-only payloads stay guarded.
    return not any(
        operation in {'u', 'r'} and 'c' in operations[index + 1:]
        for index, operation in enumerate(operations)
    )


def _record_successful_maatouch_payload(device, builder, content):
    active_contacts = set(getattr(device, '_maatouch_active_contacts', set()))
    for operation, contact in _maatouch_payload_operations(builder, content):
        if operation == 'r':
            active_contacts.clear()
        elif operation in {'d', 'm'} and contact is not None:
            active_contacts.add(contact)
        elif operation == 'u' and contact is not None:
            active_contacts.discard(contact)
    device._maatouch_active_contacts = active_contacts


def _validate_managed_crop_touch_authorization(device):
    if managed_screenshot_crop_from_environment() is None:
        return

    _assert_managed_crop_landscape_frame_fresh(device)
    captured_at = getattr(device, '_managed_crop_touch_authorized_at', None)
    generation = getattr(
        device,
        '_managed_crop_touch_authorized_generation',
        None,
    )
    if (
        captured_at is None
        or time.monotonic() - captured_at > 15
        or captured_at
        != getattr(device, '_managed_crop_landscape_frame_at', None)
        or generation
        != getattr(device, '_managed_crop_frame_generation', None)
    ):
        raise ScriptError(
            'Managed phone touch authorization expired before MaaTouch send'
        )

    budget = getattr(device, '_managed_crop_touch_budget', 0)
    if budget <= 0:
        raise ScriptError(
            'Managed phone touch requires a new landscape screenshot'
        )


def _complete_managed_crop_touch_authorization(device):
    if managed_screenshot_crop_from_environment() is None:
        return
    budget = getattr(device, '_managed_crop_touch_budget', 0)
    if budget <= 0:
        raise ScriptError('Managed phone touch authorization is already consumed')
    device._managed_crop_touch_budget = budget - 1


class MaaTouch(Connection):
    """
    Control method that implements the same as scrcpy and has an interface similar to minitouch.
    https://github.com/MaaAssistantArknights/MaaTouch
    """
    max_x: int
    max_y: int
    _maatouch_stream: socket.socket = None
    _maatouch_stream_storage = None
    _maatouch_init_thread = None
    _maatouch_orientation: int = None

    @cached_property
    @retry
    def _maatouch_builder(self):
        self.maatouch_init()
        return MaatouchBuilder(self)

    @property
    def maatouch_builder(self):
        # Wait init thread
        if self._maatouch_init_thread is not None:
            self._maatouch_init_thread.join()
            del self._maatouch_init_thread
            self._maatouch_init_thread = None

        return self._maatouch_builder

    def early_maatouch_init(self):
        """
        Start a thread to init maatouch connection while the Alas instance just starting to take screenshots
        This would speed up the first click 0.2 ~ 0.4s.
        """
        if has_cached_property(self, '_maatouch_builder'):
            return

        def early_maatouch_init_func():
            _ = self._maatouch_builder

        thread = threading.Thread(target=early_maatouch_init_func, daemon=True)
        self._maatouch_init_thread = thread
        thread.start()

    def on_orientation_change_maatouch(self):
        """
        MaaTouch caches devices orientation at its startup
        A restart is required when orientation changed
        """
        if managed_screenshot_crop_from_environment() is not None:
            # A fixed crop is tied to one tested landscape canvas. Android can
            # briefly report portrait while the screen sleeps or wakes; that
            # transient must not replace a verified landscape touch stream.
            return
        if self._maatouch_orientation is None:
            return
        if self.orientation == self._maatouch_orientation:
            return

        logger.info(f'Orientation changed {self._maatouch_orientation} => {self.orientation}, re-init MaaTouch')
        del_cached_property(self, '_maatouch_builder')
        self.early_maatouch_init()

    def maatouch_init(self):
        logger.hr('MaaTouch init')
        max_x, max_y = 1280, 720
        max_contacts = 2
        max_pressure = 50

        # Try to close existing stream
        if self._maatouch_stream is not None:
            pending_contacts = set(
                getattr(self, '_maatouch_pending_release_contacts', set())
            )
            pending_contacts.update(
                getattr(self, '_maatouch_active_contacts', set())
            )
            self._maatouch_pending_release_contacts = pending_contacts
            self._maatouch_active_contacts = set()
            try:
                self._maatouch_stream.close()
            except Exception as e:
                logger.error(e)
            del self._maatouch_stream
        if self._maatouch_stream_storage is not None:
            del self._maatouch_stream_storage

        # MaaTouch caches devices orientation at its startup
        super(MaaTouch, self).get_orientation()
        self._maatouch_orientation = self.orientation

        # CLASSPATH=/data/local/tmp/maatouch app_process / com.shxyke.MaaTouch.App
        stream = self.adb_shell(
            ['CLASSPATH=/data/local/tmp/maatouch', 'app_process', '/', 'com.shxyke.MaaTouch.App'],
            stream=True,
            recvall=False
        )
        # Prevent shell stream from being deleted causing socket close
        self._maatouch_stream_storage = stream
        stream = stream.conn
        stream.settimeout(10)
        self._maatouch_stream = stream
        self._maatouch_active_contacts = set()

        retry_timeout = Timer(5).start()
        while 1:
            # v <version>
            # protocol version, usually it is 1. needn't use this
            # get maatouch server info
            socket_out = stream.makefile()

            # ^ <max-contacts> <max-x> <max-y> <max-pressure>
            out = socket_out.readline().replace("\n", "").replace("\r", "")
            logger.info(out)
            if out.strip() == 'Aborted':
                stream.close()
                raise MaaTouchNotInstalledError(
                    'Received "Aborted" MaaTouch, '
                    'probably because MaaTouch is not installed'
                )
            try:
                _, max_contacts, max_x, max_y, max_pressure = out.split(" ")
                break
            except ValueError:
                stream.close()
                if retry_timeout.reached():
                    raise MaaTouchNotInstalledError(
                        'Received empty data from MaaTouch, '
                        'probably because MaaTouch is not installed'
                    )
                else:
                    # maatouch may not start that fast
                    self.sleep(1)
                    continue

        # self.max_contacts = max_contacts
        self.max_x = int(max_x)
        self.max_y = int(max_y)
        # self.max_pressure = max_pressure

        # $ <pid>
        out = socket_out.readline().replace("\n", "").replace("\r", "")
        logger.info(out)
        # _, pid = out.split(" ")
        # self._maatouch_pid = pid

        # Releasing contacts has no coordinates, so it is safe and necessary
        # even when this new stream reports portrait axes and will be rejected.
        self._maatouch_release_pending_contacts(stream)
        if (
            managed_screenshot_crop_from_environment() is not None
            and self.max_x <= self.max_y
        ):
            stream.close()
            self._maatouch_stream = None
            self._maatouch_stream_storage = None
            raise ScriptError(
                'Managed phone crop requires landscape MaaTouch axes'
            )

        self._maatouch_stream_generation = (
            getattr(self, '_maatouch_stream_generation', 0) + 1
        )
        logger.info("MaaTouch stream connected")
        logger.info(
            "max_contact: {}; max_x: {}; max_y: {}; max_pressure: {}".format(
                max_contacts, max_x, max_y, max_pressure
            )
        )

    def _maatouch_release_pending_contacts(self, stream):
        pending_contacts = sorted(
            getattr(self, '_maatouch_pending_release_contacts', set())
        )
        if not pending_contacts:
            return
        content = ''.join(
            f'u {contact}\nc\n' for contact in pending_contacts
        ).encode('utf-8')
        stream.sendall(content)
        stream.recv(0)
        self._maatouch_pending_release_contacts = set()

    def _maatouch_poison_stream(self, builder, content):
        pending_contacts = set(
            getattr(self, '_maatouch_pending_release_contacts', set())
        )
        pending_contacts.update(
            getattr(self, '_maatouch_active_contacts', set())
        )
        pending_contacts.update(
            contact
            for operation, contact in _maatouch_payload_operations(
                builder,
                content,
            )
            if operation in {'d', 'm', 'u'} and contact is not None
        )
        contact = getattr(builder, 'contact', None)
        if contact is not None:
            pending_contacts.add(contact)
        self._maatouch_pending_release_contacts = pending_contacts
        self._maatouch_active_contacts = set()

        stream = getattr(self, '_maatouch_stream', None)
        if stream is not None:
            try:
                stream.close()
            except Exception as error:
                logger.warning(f'Failed to close MaaTouch stream: {error}')
        self._maatouch_stream = None
        self._maatouch_stream_storage = None
        del_cached_property(self, '_maatouch_builder')

    def _maatouch_ensure_stream(self):
        if getattr(self, '_maatouch_stream', None) is not None:
            return
        del_cached_property(self, '_maatouch_builder')
        _ = self.maatouch_builder

    def maatouch_send(self, builder: MaatouchBuilder):
        # This is the non-bypassable wire seam. Direct builders used by the
        # map joystick do not pass through Control.click/swipe wrappers. A
        # stale frame must block down/move, but never block an up/reset that
        # releases an already active contact after a capture failure.
        content = builder.to_minitouch()
        if (
            getattr(self, '_maatouch_pending_release_contacts', set())
            and getattr(self, '_maatouch_stream', None) is None
        ):
            self._maatouch_ensure_stream()
        if _maatouch_payload_requires_landscape_frame(builder, content):
            try:
                _assert_managed_crop_landscape_frame_fresh(self)
            except Exception:
                builder.clear()
                raise
        if getattr(self, '_maatouch_stream', None) is None:
            self._maatouch_ensure_stream()
        # logger.info("send operation: {}".format(content.replace("\n", "\\n")))
        byte_content = content.encode('utf-8')
        try:
            self._maatouch_stream.sendall(byte_content)
            self._maatouch_stream.recv(0)
            self.sleep(builder.delay / 1000 + builder.DEFAULT_DELAY)
        except Exception:
            self._maatouch_poison_stream(builder, content)
            builder.clear()
            raise
        _record_successful_maatouch_payload(self, builder, content)
        builder.clear()

    def _maatouch_release_contact_after_failure(self, builder):
        """Best-effort release without replaying unsent move commands."""
        contact = builder.contact
        builder.clear()
        cleanup = MaatouchBuilder(self, contact=contact)
        cleanup.up().commit()
        try:
            cleanup.send()
        except Exception as error:
            logger.warning(f'Failed to release MaaTouch contact: {error}')

    def maatouch_install(self):
        logger.hr('MaaTouch install')
        self.adb_push(self.config.MAATOUCH_FILEPATH_LOCAL, self.config.MAATOUCH_FILEPATH_REMOTE)

    def maatouch_uninstall(self):
        logger.hr('MaaTouch uninstall')
        self.adb_shell(["rm", self.config.MAATOUCH_FILEPATH_REMOTE])

    @retry
    def click_maatouch(self, x, y):
        builder = self.maatouch_builder
        _validate_managed_crop_touch_authorization(self)
        builder.down(x, y).commit()
        if managed_screenshot_crop_from_environment() is not None:
            # Some Unity buttons ignore a zero-duration pulse on the managed
            # phone canvas. Hold for one short frame without changing normal
            # emulator behavior.
            builder.wait(50)
        builder.up().commit()
        builder.send()
        _complete_managed_crop_touch_authorization(self)

    @retry
    def long_click_maatouch(self, x, y, duration=1.0):
        duration = int(duration * 1000)
        builder = self.maatouch_builder
        _validate_managed_crop_touch_authorization(self)
        builder.down(x, y).commit().wait(duration)
        builder.up().commit()
        builder.send()
        _complete_managed_crop_touch_authorization(self)

    @retry
    def swipe_maatouch(self, p1, p2):
        points = insert_swipe(p0=p1, p3=p2)
        builder = self.maatouch_builder
        _validate_managed_crop_touch_authorization(self)
        contact_may_be_active = False
        try:
            builder.down(*points[0]).commit().wait(10)
            contact_may_be_active = True
            builder.send()

            for point in points[1:]:
                builder.move(*point).commit().wait(10)
            builder.send()

            builder.up().commit()
            builder.send()
            contact_may_be_active = False
        except Exception:
            if contact_may_be_active:
                self._maatouch_release_contact_after_failure(builder)
            raise
        _complete_managed_crop_touch_authorization(self)

    @retry
    def drag_maatouch(self, p1, p2, point_random=(-10, -10, 10, 10)):
        p1 = np.array(p1) - random_rectangle_point(point_random)
        p2 = np.array(p2) - random_rectangle_point(point_random)
        points = insert_swipe(p0=p1, p3=p2, speed=20)
        builder = self.maatouch_builder
        _validate_managed_crop_touch_authorization(self)
        contact_may_be_active = False
        try:
            builder.down(*points[0]).commit().wait(10)
            contact_may_be_active = True
            builder.send()

            for point in points[1:]:
                builder.move(*point).commit().wait(10)
            builder.send()

            builder.move(*p2).commit().wait(140)
            builder.move(*p2).commit().wait(140)
            builder.send()

            builder.up().commit()
            builder.send()
            contact_may_be_active = False
        except Exception:
            if contact_may_be_active:
                self._maatouch_release_contact_after_failure(builder)
            raise
        _complete_managed_crop_touch_authorization(self)


if __name__ == '__main__':
    self = MaaTouch('src')
    self.maatouch_uninstall()
