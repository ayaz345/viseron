"""FFmpeg camera."""
from __future__ import annotations

import datetime
import os
import time
from threading import Event
from typing import TYPE_CHECKING

import cv2
import voluptuous as vol

from viseron import Viseron
from viseron.const import ENV_CUDA_SUPPORTED, ENV_VAAPI_SUPPORTED
from viseron.domains.camera import (
    BASE_CONFIG_SCHEMA as BASE_CAMERA_CONFIG_SCHEMA,
    DEFAULT_RECORDER,
    RECORDER_SCHEMA as BASE_RECORDER_SCHEMA,
    AbstractCamera,
)
from viseron.domains.camera.const import (
    CONFIG_EXTENSION,
    DOMAIN,
    EVENT_CAMERA_STARTED,
    EVENT_CAMERA_STOPPED,
)
from viseron.exceptions import DomainNotReady, FFprobeError, FFprobeTimeout
from viseron.helpers.validators import CameraIdentifier, CoerceNoneToDict, Maybe
from viseron.watchdog.thread_watchdog import RestartableThread

from .const import (
    COMPONENT,
    CONFIG_AUDIO_CODEC,
    CONFIG_CODEC,
    CONFIG_FFMPEG_LOGLEVEL,
    CONFIG_FFMPEG_RECOVERABLE_ERRORS,
    CONFIG_FFPROBE_LOGLEVEL,
    CONFIG_FPS,
    CONFIG_FRAME_TIMEOUT,
    CONFIG_GLOBAL_ARGS,
    CONFIG_HEIGHT,
    CONFIG_HOST,
    CONFIG_HWACCEL_ARGS,
    CONFIG_INPUT_ARGS,
    CONFIG_PASSWORD,
    CONFIG_PATH,
    CONFIG_PIX_FMT,
    CONFIG_PORT,
    CONFIG_PROTOCOL,
    CONFIG_RAW_COMMAND,
    CONFIG_RECORDER,
    CONFIG_RECORDER_AUDIO_CODEC,
    CONFIG_RECORDER_AUDIO_FILTERS,
    CONFIG_RECORDER_CODEC,
    CONFIG_RECORDER_HWACCEL_ARGS,
    CONFIG_RECORDER_OUPTUT_ARGS,
    CONFIG_RECORDER_VIDEO_FILTERS,
    CONFIG_RTSP_TRANSPORT,
    CONFIG_SEGMENTS_FOLDER,
    CONFIG_STREAM_FORMAT,
    CONFIG_SUBSTREAM,
    CONFIG_USERNAME,
    CONFIG_VIDEO_FILTERS,
    CONFIG_WIDTH,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CODEC,
    DEFAULT_FFMPEG_LOGLEVEL,
    DEFAULT_FFMPEG_RECOVERABLE_ERRORS,
    DEFAULT_FFPROBE_LOGLEVEL,
    DEFAULT_FPS,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_GLOBAL_ARGS,
    DEFAULT_HEIGHT,
    DEFAULT_HWACCEL_ARGS,
    DEFAULT_INPUT_ARGS,
    DEFAULT_PASSWORD,
    DEFAULT_PIX_FMT,
    DEFAULT_PROTOCOL,
    DEFAULT_RAW_COMMAND,
    DEFAULT_RECORDER_AUDIO_CODEC,
    DEFAULT_RECORDER_AUDIO_FILTERS,
    DEFAULT_RECORDER_CODEC,
    DEFAULT_RECORDER_HWACCEL_ARGS,
    DEFAULT_RECORDER_OUTPUT_ARGS,
    DEFAULT_RECORDER_VIDEO_FILTERS,
    DEFAULT_RTSP_TRANSPORT,
    DEFAULT_SEGMENTS_FOLDER,
    DEFAULT_STREAM_FORMAT,
    DEFAULT_SUBSTREAM,
    DEFAULT_USERNAME,
    DEFAULT_VIDEO_FILTERS,
    DEFAULT_WIDTH,
    DESC_AUDIO_CODEC,
    DESC_CODEC,
    DESC_FFMPEG_LOGLEVEL,
    DESC_FFMPEG_RECOVERABLE_ERRORS,
    DESC_FFPROBE_LOGLEVEL,
    DESC_FPS,
    DESC_FRAME_TIMEOUT,
    DESC_GLOBAL_ARGS,
    DESC_HEIGHT,
    DESC_HOST,
    DESC_HWACCEL_ARGS,
    DESC_INPUT_ARGS,
    DESC_PASSWORD,
    DESC_PATH,
    DESC_PIX_FMT,
    DESC_PORT,
    DESC_PROTOCOL,
    DESC_RAW_COMMAND,
    DESC_RECORDER,
    DESC_RECORDER_AUDIO_CODEC,
    DESC_RECORDER_AUDIO_FILTERS,
    DESC_RECORDER_CODEC,
    DESC_RECORDER_FFMPEG_LOGLEVEL,
    DESC_RECORDER_HWACCEL_ARGS,
    DESC_RECORDER_OUTPUT_ARGS,
    DESC_RECORDER_VIDEO_FILTERS,
    DESC_RTSP_TRANSPORT,
    DESC_SEGMENTS_FOLDER,
    DESC_STREAM_FORMAT,
    DESC_SUBSTREAM,
    DESC_USERNAME,
    DESC_VIDEO_FILTERS,
    DESC_WIDTH,
    FFMPEG_LOGLEVELS,
    HWACCEL_VAAPI,
    STREAM_FORMAT_MAP,
)
from .recorder import Recorder
from .stream import Stream

if TYPE_CHECKING:
    from viseron.components.nvr.nvr import FrameIntervalCalculator
    from viseron.domains.camera.shared_frames import SharedFrame
    from viseron.domains.object_detector.detected_object import DetectedObject


def get_default_hwaccel_args() -> list[str]:
    """Return hardware acceleration args for FFmpeg."""
    # Dont enable VA-API if CUDA is available
    if (
        os.getenv(ENV_VAAPI_SUPPORTED) == "true"
        and os.getenv(ENV_CUDA_SUPPORTED) != "true"
    ):
        return HWACCEL_VAAPI
    return DEFAULT_HWACCEL_ARGS


STREAM_SCEHMA_DICT = {
    vol.Required(CONFIG_PATH, description=DESC_PATH): vol.All(str, vol.Length(min=1)),
    vol.Required(CONFIG_PORT, description=DESC_PORT): vol.All(int, vol.Range(min=1)),
    vol.Optional(
        CONFIG_STREAM_FORMAT,
        default=DEFAULT_STREAM_FORMAT,
        description=DESC_STREAM_FORMAT,
    ): vol.In(STREAM_FORMAT_MAP.keys()),
    vol.Optional(
        CONFIG_PROTOCOL, default=DEFAULT_PROTOCOL, description=DESC_PROTOCOL
    ): Maybe(vol.Any("rtsp", "rtsps", "rtmp", "http", "https")),
    vol.Optional(CONFIG_WIDTH, default=DEFAULT_WIDTH, description=DESC_WIDTH): Maybe(
        int
    ),
    vol.Optional(CONFIG_HEIGHT, default=DEFAULT_HEIGHT, description=DESC_HEIGHT): Maybe(
        int
    ),
    vol.Optional(CONFIG_FPS, default=DEFAULT_FPS, description=DESC_FPS): Maybe(
        vol.All(int, vol.Range(min=1))
    ),
    vol.Optional(
        CONFIG_INPUT_ARGS, default=DEFAULT_INPUT_ARGS, description=DESC_INPUT_ARGS
    ): Maybe(list),
    vol.Optional(
        CONFIG_HWACCEL_ARGS,
        default=get_default_hwaccel_args(),
        description=DESC_HWACCEL_ARGS,
    ): Maybe(list),
    vol.Optional(CONFIG_CODEC, default=DEFAULT_CODEC, description=DESC_CODEC): str,
    vol.Optional(
        CONFIG_AUDIO_CODEC, default=DEFAULT_AUDIO_CODEC, description=DESC_AUDIO_CODEC
    ): Maybe(str),
    vol.Optional(
        CONFIG_RTSP_TRANSPORT,
        default=DEFAULT_RTSP_TRANSPORT,
        description=DESC_RTSP_TRANSPORT,
    ): vol.Any("tcp", "udp", "udp_multicast", "http"),
    vol.Optional(
        CONFIG_VIDEO_FILTERS,
        default=DEFAULT_VIDEO_FILTERS,
        description=DESC_VIDEO_FILTERS,
    ): list,
    vol.Optional(
        CONFIG_PIX_FMT, default=DEFAULT_PIX_FMT, description=DESC_PIX_FMT
    ): vol.Any("nv12", "yuv420p"),
    vol.Optional(
        CONFIG_FRAME_TIMEOUT,
        default=DEFAULT_FRAME_TIMEOUT,
        description=DESC_FRAME_TIMEOUT,
    ): vol.All(int, vol.Range(1, 60)),
    vol.Optional(
        CONFIG_RAW_COMMAND,
        default=DEFAULT_RAW_COMMAND,
        description=DESC_RAW_COMMAND,
    ): Maybe(str),
}

FFMPEG_LOGLEVEL_SCEHMA = vol.Schema(vol.In(FFMPEG_LOGLEVELS.keys()))

RECORDER_SCHEMA = BASE_RECORDER_SCHEMA.extend(
    {
        vol.Optional(
            CONFIG_RECORDER_HWACCEL_ARGS,
            default=DEFAULT_RECORDER_HWACCEL_ARGS,
            description=DESC_RECORDER_HWACCEL_ARGS,
        ): [str],
        vol.Optional(
            CONFIG_RECORDER_CODEC,
            default=DEFAULT_RECORDER_CODEC,
            description=DESC_RECORDER_CODEC,
        ): str,
        vol.Optional(
            CONFIG_RECORDER_AUDIO_CODEC,
            default=DEFAULT_RECORDER_AUDIO_CODEC,
            description=DESC_RECORDER_AUDIO_CODEC,
        ): Maybe(str),
        vol.Optional(
            CONFIG_RECORDER_VIDEO_FILTERS,
            default=DEFAULT_RECORDER_VIDEO_FILTERS,
            description=DESC_RECORDER_VIDEO_FILTERS,
        ): [str],
        vol.Optional(
            CONFIG_RECORDER_AUDIO_FILTERS,
            default=DEFAULT_RECORDER_AUDIO_FILTERS,
            description=DESC_RECORDER_AUDIO_FILTERS,
        ): [str],
        vol.Optional(
            CONFIG_RECORDER_OUPTUT_ARGS,
            default=DEFAULT_RECORDER_OUTPUT_ARGS,
            description=DESC_RECORDER_OUTPUT_ARGS,
        ): [str],
        vol.Optional(
            CONFIG_SEGMENTS_FOLDER,
            default=DEFAULT_SEGMENTS_FOLDER,
            description=DESC_SEGMENTS_FOLDER,
        ): str,
        vol.Optional(
            CONFIG_FFMPEG_LOGLEVEL,
            default=DEFAULT_FFMPEG_LOGLEVEL,
            description=DESC_RECORDER_FFMPEG_LOGLEVEL,
        ): FFMPEG_LOGLEVEL_SCEHMA,
    }
)

CAMERA_SCHEMA = BASE_CAMERA_CONFIG_SCHEMA.extend(STREAM_SCEHMA_DICT)

CAMERA_SCHEMA = CAMERA_SCHEMA.extend(
    {
        vol.Required(CONFIG_HOST, description=DESC_HOST): str,
        vol.Optional(
            CONFIG_USERNAME, default=DEFAULT_USERNAME, description=DESC_USERNAME
        ): Maybe(str),
        vol.Optional(
            CONFIG_PASSWORD, default=DEFAULT_PASSWORD, description=DESC_PASSWORD
        ): Maybe(str),
        vol.Optional(
            CONFIG_GLOBAL_ARGS,
            default=DEFAULT_GLOBAL_ARGS,
            description=DESC_GLOBAL_ARGS,
        ): list,
        vol.Optional(
            CONFIG_SUBSTREAM, default=DEFAULT_SUBSTREAM, description=DESC_SUBSTREAM
        ): Maybe(vol.Schema(STREAM_SCEHMA_DICT)),
        vol.Optional(
            CONFIG_FFMPEG_LOGLEVEL,
            default=DEFAULT_FFMPEG_LOGLEVEL,
            description=DESC_FFMPEG_LOGLEVEL,
        ): FFMPEG_LOGLEVEL_SCEHMA,
        vol.Optional(
            CONFIG_FFMPEG_RECOVERABLE_ERRORS,
            default=DEFAULT_FFMPEG_RECOVERABLE_ERRORS,
            description=DESC_FFMPEG_RECOVERABLE_ERRORS,
        ): [str],
        vol.Optional(
            CONFIG_FFPROBE_LOGLEVEL,
            default=DEFAULT_FFPROBE_LOGLEVEL,
            description=DESC_FFPROBE_LOGLEVEL,
        ): FFMPEG_LOGLEVEL_SCEHMA,
        vol.Optional(
            CONFIG_RECORDER, default=DEFAULT_RECORDER, description=DESC_RECORDER
        ): vol.All(CoerceNoneToDict(), RECORDER_SCHEMA),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        CameraIdentifier(): CAMERA_SCHEMA,
    }
)


def setup(vis: Viseron, config, identifier) -> bool:
    """Set up the ffmpeg camera domain."""
    try:
        Camera(vis, config[identifier], identifier)
    except (FFprobeError, FFprobeTimeout) as error:
        raise DomainNotReady from error
    return True


class Camera(AbstractCamera):
    """Represents a camera which is consumed via FFmpeg."""

    def __init__(self, vis: Viseron, config, identifier) -> None:
        self._poll_timer = datetime.datetime.now().timestamp()
        self._frame_reader = None
        # Stream must be initialized before super().__init__ is called as it raises
        # FFprobeError/FFprobeTimeout which is caught in setup() and re-raised as
        # DomainNotReady
        self.stream = Stream(config, self, identifier)

        super().__init__(vis, COMPONENT, config, identifier)
        self._capture_frames = False
        self._thread_stuck = False
        self.resolution = None
        self.decode_error = Event()

        if cv2.ocl.haveOpenCL():
            cv2.ocl.setUseOpenCL(True)
        vis.data[COMPONENT][self.identifier] = self
        self._recorder = Recorder(vis, config, self)

        self.initialize_camera()
        vis.register_domain(DOMAIN, self.identifier, self)

    def _create_frame_reader(self):
        """Return a frame reader thread."""
        return RestartableThread(
            name=f"viseron.camera.{self.identifier}",
            target=self.read_frames,
            poll_method=self.poll_method,
            poll_target=self.poll_target,
            daemon=True,
            register=True,
            restart_method=self.start_camera,
        )

    def initialize_camera(self) -> None:
        """Start processing of camera frames."""
        self._logger.debug(f"Initializing camera {self.name}")

        self.resolution = self.stream.width, self.stream.height
        self._logger.debug(
            f"Resolution: {self.resolution[0]}x{self.resolution[1]} "
            f"@ {self.stream.fps} FPS"
        )

        self._logger.debug(f"Camera {self.name} initialized")

    def read_frames(self) -> None:
        """Read frames from camera."""
        self.decode_error.clear()
        self._poll_timer = datetime.datetime.now().timestamp()
        empty_frames = 0
        self._thread_stuck = False

        self.stream.start_pipe()

        while self._capture_frames:
            if self.decode_error.is_set():
                self._poll_timer = datetime.datetime.now().timestamp()
                self.connected = False
                time.sleep(5)
                self._logger.error("Restarting frame pipe")
                self.stream.close_pipe()
                self.stream.start_pipe()
                self.decode_error.clear()
                empty_frames = 0

            self.current_frame = self.stream.read()
            if self.current_frame:
                self.connected = True
                empty_frames = 0
                self._poll_timer = datetime.datetime.now().timestamp()
                self._data_stream.publish_data(
                    self.frame_bytes_topic, self.current_frame
                )
                continue

            if self._thread_stuck:
                return

            if self.stream.poll() is not None:
                self._logger.error("Frame reader process has exited")
                self.decode_error.set()
                continue

            empty_frames += 1
            if empty_frames >= 10:
                self._logger.error("Did not receive a frame")
                self.decode_error.set()

        self.connected = False
        self.stream.close_pipe()
        self._logger.debug("Frame reader stopped")

    def poll_target(self) -> None:
        """Close pipe when RestartableThread.poll_timeout has been reached."""
        self._logger.error("Timeout waiting for frame")
        self._thread_stuck = True
        self.stop_camera()

    def poll_method(self) -> bool:
        """Return true on frame timeout for RestartableThread to trigger a restart."""
        now = datetime.datetime.now().timestamp()

        # Make sure we timeout at some point if we never get the first frame.
        if now - self._poll_timer > (DEFAULT_FRAME_TIMEOUT * 2):
            return True

        if not self.connected:
            return False

        return now - self._poll_timer > self._config[CONFIG_FRAME_TIMEOUT]

    def calculate_output_fps(self, scanners: list[FrameIntervalCalculator]) -> None:
        """Calculate the camera output fps based on registered frame scanners.

        Overrides AbstractCamera.calculate_output_fps since we can't use the default
        implementation if the user has entered a raw pipeline.
        """
        if self._config[CONFIG_RAW_COMMAND]:
            self.output_fps = self.stream.fps
            return

        return super().calculate_output_fps(scanners)

    def start_camera(self) -> None:
        """Start capturing frames from camera."""
        self._logger.debug("Starting capture thread")
        self._capture_frames = True
        if not self._frame_reader or not self._frame_reader.is_alive():
            self._frame_reader = self._create_frame_reader()
            self._frame_reader.start()
            self._vis.dispatch_event(
                EVENT_CAMERA_STARTED.format(camera_identifier=self.identifier),
                None,
            )

    def stop_camera(self) -> None:
        """Release the connection to the camera."""
        self._logger.debug("Stopping capture thread")
        self._capture_frames = False
        if self._frame_reader:
            self._frame_reader.stop()
            self._frame_reader.join(timeout=5)
            if self._frame_reader.is_alive():
                self._logger.debug("Timed out trying to stop camera. Killing pipe")
                self.stream.close_pipe()

        self._vis.dispatch_event(
            EVENT_CAMERA_STOPPED.format(camera_identifier=self.identifier),
            None,
        )
        if self.is_recording:
            self.stop_recorder()

    def start_recorder(
        self, shared_frame: SharedFrame, objects_in_fov: list[DetectedObject] | None
    ) -> None:
        """Start camera recorder."""
        self._recorder.start(
            shared_frame, objects_in_fov if objects_in_fov else [], self.resolution
        )

    def stop_recorder(self) -> None:
        """Stop camera recorder."""
        self._recorder.stop(self.recorder.active_recording)

    @property
    def output_fps(self):
        """Set stream output fps."""
        return self.stream.output_fps

    @output_fps.setter
    def output_fps(self, fps) -> None:
        self.stream.output_fps = fps

    @property
    def resolution(self):
        """Return stream resolution."""
        return self._resolution

    @resolution.setter
    def resolution(self, resolution) -> None:
        """Return stream resolution."""
        self._resolution = resolution

    @property
    def extension(self) -> str:
        """Return recording file extension."""
        return self._config[CONFIG_RECORDER][CONFIG_EXTENSION]

    @property
    def recorder(self) -> Recorder:
        """Return recorder instance."""
        return self._recorder

    @property
    def is_recording(self):
        """Return recording status."""
        return self._recorder.is_recording

    @property
    def is_on(self):
        """Return if camera is on."""
        return self._frame_reader.is_alive() if self._frame_reader else False
