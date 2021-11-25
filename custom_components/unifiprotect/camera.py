"""Support for Ubiquiti's Unifi Protect NVR."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Generator, Optional

from homeassistant.components.camera import SUPPORT_STREAM, Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from pyunifiprotect.api import ProtectApiClient
from pyunifiprotect.data import Camera as UnifiCamera
from pyunifiprotect.data.devices import CameraChannel
from pyunifiprotect.data.types import (
    DoorbellMessageType,
    IRLEDMode,
    RecordingMode,
    VideoMode,
)
from pyunifiprotect.utils import to_js_time, utc_now

from custom_components.unifiprotect.data import UnifiProtectData

from .const import (
    ATTR_CAMERA_ID,
    ATTR_CHIME_DURATION,
    ATTR_CHIME_ENABLED,
    ATTR_IS_DARK,
    ATTR_MIC_SENSITIVITY,
    ATTR_PRIVACY_MODE,
    ATTR_UP_SINCE,
    ATTR_WDR_VALUE,
    ATTR_ZOOM_POSITION,
    DEFAULT_BRAND,
    DOMAIN,
    SERVICE_SET_DOORBELL_CHIME_DURAION,
    SERVICE_SET_DOORBELL_LCD_MESSAGE,
    SERVICE_SET_HDR_MODE,
    SERVICE_SET_HIGHFPS_VIDEO_MODE,
    SERVICE_SET_IR_MODE,
    SERVICE_SET_MIC_VOLUME,
    SERVICE_SET_PRIVACY_MODE,
    SERVICE_SET_RECORDING_MODE,
    SERVICE_SET_STATUS_LIGHT,
    SERVICE_SET_WDR_VALUE,
    SERVICE_SET_ZOOM_POSITION,
    SET_DOORBELL_CHIME_DURATION_SCHEMA,
    SET_DOORBELL_LCD_MESSAGE_SCHEMA,
    SET_HDR_MODE_SCHEMA,
    SET_HIGHFPS_VIDEO_MODE_SCHEMA,
    SET_IR_MODE_SCHEMA,
    SET_MIC_VOLUME_SCHEMA,
    SET_PRIVACY_MODE_SCHEMA,
    SET_RECORDING_MODE_SCHEMA,
    SET_STATUS_LIGHT_SCHEMA,
    SET_WDR_VALUE_SCHEMA,
    SET_ZOOM_POSITION_SCHEMA,
)
from .entity import UnifiProtectEntity
from .models import UnifiProtectEntryData

_LOGGER = logging.getLogger(__name__)


def get_camera_channels(
    protect: ProtectApiClient,
) -> Generator[tuple[UnifiCamera, CameraChannel, bool], None, None]:

    for camera in protect.bootstrap.cameras.values():
        is_default = True
        for channel in camera.channels:
            if channel.is_rtsp_enabled:
                yield camera, channel, is_default
                is_default = False

        # no RTSP enabled use first channel with no stream
        if is_default:
            yield camera, camera.channels[0], True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Discover cameras on a Unifi Protect NVR."""
    entry_data: UnifiProtectEntryData = hass.data[DOMAIN][entry.entry_id]
    protect = entry_data.protect
    protect_data = entry_data.protect_data
    disable_stream = entry_data.disable_stream

    async_add_entities(
        [
            UnifiProtectCamera(
                protect,
                protect_data,
                camera,
                channel,
                is_default,
                disable_stream,
            )
            for camera, channel, is_default in get_camera_channels(protect)
        ]
    )

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_SET_RECORDING_MODE,
        SET_RECORDING_MODE_SCHEMA,
        "async_set_recording_mode",
    )

    platform.async_register_entity_service(
        SERVICE_SET_IR_MODE, SET_IR_MODE_SCHEMA, "async_set_ir_mode"
    )

    platform.async_register_entity_service(
        SERVICE_SET_STATUS_LIGHT, SET_STATUS_LIGHT_SCHEMA, "async_set_status_light"
    )

    platform.async_register_entity_service(
        SERVICE_SET_HDR_MODE, SET_HDR_MODE_SCHEMA, "async_set_hdr_mode"
    )

    platform.async_register_entity_service(
        SERVICE_SET_HIGHFPS_VIDEO_MODE,
        SET_HIGHFPS_VIDEO_MODE_SCHEMA,
        "async_set_highfps_video_mode",
    )

    platform.async_register_entity_service(
        SERVICE_SET_DOORBELL_LCD_MESSAGE,
        SET_DOORBELL_LCD_MESSAGE_SCHEMA,
        "async_set_doorbell_lcd_message",
    )

    platform.async_register_entity_service(
        SERVICE_SET_MIC_VOLUME, SET_MIC_VOLUME_SCHEMA, "async_set_mic_volume"
    )

    platform.async_register_entity_service(
        SERVICE_SET_PRIVACY_MODE, SET_PRIVACY_MODE_SCHEMA, "async_set_privacy_mode"
    )

    platform.async_register_entity_service(
        SERVICE_SET_ZOOM_POSITION, SET_ZOOM_POSITION_SCHEMA, "async_set_zoom_position"
    )

    platform.async_register_entity_service(
        SERVICE_SET_WDR_VALUE, SET_WDR_VALUE_SCHEMA, "async_set_wdr_value"
    )

    platform.async_register_entity_service(
        SERVICE_SET_DOORBELL_CHIME_DURAION,
        SET_DOORBELL_CHIME_DURATION_SCHEMA,
        "async_set_doorbell_chime_duration",
    )


class UnifiProtectCamera(UnifiProtectEntity, Camera):
    """A Ubiquiti Unifi Protect Camera."""

    def __init__(
        self,
        protect: ProtectApiClient,
        protect_data: UnifiProtectData,
        camera: UnifiCamera,
        channel: CameraChannel,
        is_default: bool,
        disable_stream: bool,
    ):
        """Initialize an Unifi camera."""
        super().__init__(protect, protect_data, camera, None)

        self.device: UnifiCamera = camera
        self.channel = channel
        self._disable_stream = disable_stream
        self._last_image = None
        self._async_set_stream_source()
        self._attr_unique_id = f"{self.device.id}_{self.device.mac}_{self.channel.id}"
        # only the default (first) channel is enabled by default
        self._attr_entity_registry_enabled_default = is_default
        self._attr_name = f"{self.device.name} {self.channel.name}"

    @callback
    def _async_set_stream_source(self):
        disable_stream = self._disable_stream
        if not self.channel.is_rtsp_enabled:
            disable_stream = False

        self._stream_source = None if disable_stream else self.channel.rtsps_url
        self._attr_supported_features = SUPPORT_STREAM if self._stream_source else 0

    @callback
    def _async_updated_event(self):
        if self.protect_data.last_update_success:
            self.device = self.protect.bootstrap.cameras[self.device.id]
            for channel in self.device.channels:
                if channel.id == self.channel.id:
                    self.channel = channel
                    break
            self._async_set_stream_source()

        self._attr_available = (
            self.device.is_connected and self.protect_data.last_update_success
        )
        self.async_write_ha_state()

    @property
    def supported_features(self):
        """Return supported features for this camera."""
        return self._attr_supported_features

    @property
    def motion_detection_enabled(self):
        """Camera Motion Detection Status."""
        return self.device.feature_flags.has_motion_zones and super().available

    @property
    def brand(self):
        """Return the Cameras Brand."""
        return DEFAULT_BRAND

    @property
    def model(self):
        """Return the camera model."""
        return self.device.type

    @property
    def is_recording(self):
        """Return true if the device is recording."""
        return self.device.is_connected and self.device.is_recording

    @property
    def extra_state_attributes(self):
        """Add additional Attributes to Camera."""

        return {
            **super().extra_state_attributes,
            ATTR_UP_SINCE: to_js_time(self.device.up_since),
            ATTR_CAMERA_ID: self.device.id,
            ATTR_CHIME_ENABLED: self.device.feature_flags.has_chime,
            ATTR_CHIME_DURATION: self.device.chime_duration,
            ATTR_IS_DARK: self.device.is_dark,
            ATTR_MIC_SENSITIVITY: self.device.mic_volume,
            ATTR_PRIVACY_MODE: self.device.get_privacy_zone() != None,
            ATTR_WDR_VALUE: self.device.isp_settings.wdr,
            ATTR_ZOOM_POSITION: self.device.isp_settings.zoom_position,
        }

    async def async_set_recording_mode(self, recording_mode: str) -> None:
        """Set Camera Recording Mode."""
        await self.device.set_recording_mode(RecordingMode(recording_mode))

    async def async_set_ir_mode(self, ir_mode: str) -> None:
        """Set camera ir mode."""
        await self.device.set_ir_led_model(IRLEDMode(ir_mode))

    async def async_set_status_light(self, light_on: bool) -> None:
        """Set camera Status Light."""
        await self.device.set_status_light(light_on)

    async def async_set_hdr_mode(self, hdr_on: bool) -> None:
        """Set camera HDR mode."""
        await self.device.set_hdr(hdr_on)

    async def async_set_doorbell_chime_duration(self, chime_duration: int) -> None:
        """Set Doorbell Chime duration"""
        await self.device.set_chime_duration(chime_duration)

    async def async_set_highfps_video_mode(self, high_fps_on: bool) -> None:
        """Set camera High FPS video mode."""
        await self.device.set_video_mode(
            VideoMode.HIGH_FPS if high_fps_on else VideoMode.DEFAULT
        )

    async def async_set_doorbell_lcd_message(self, message: str, duration: str) -> None:
        """Set LCD Message on Doorbell display."""

        reset_at = None
        if duration.isnumeric():
            reset_at = utc_now() + timedelta(minutes=int(duration))

        await self.device.set_lcd_text(
            DoorbellMessageType.CUSTOM_MESSAGE, message, reset_at=reset_at
        )

    async def async_set_mic_volume(self, level: int) -> None:
        """Set camera Microphone Level."""
        await self.device.set_mic_volume(level)

    async def async_set_privacy_mode(
        self, privacy_mode: bool, mic_level: int, recording_mode: str
    ) -> None:
        """Set camera Privacy mode."""
        await self.device.set_privacy(
            privacy_mode, mic_level, RecordingMode(recording_mode)
        )

    async def async_set_wdr_value(self, value: int) -> None:
        """Set camera wdr value."""
        await self.device.set_wdr_level(value)

    async def async_set_zoom_position(self, position: int) -> None:
        """Set camera Zoom Position."""
        await self.device.set_camera_zoom(position)

    async def async_enable_motion_detection(self) -> None:
        """Enable motion detection in camera."""
        if not await self.device.set_recording_mode(RecordingMode.DETECTIONS):
            return
        _LOGGER.debug("Motion Detection Enabled for Camera: %s", self.device.name)

    async def async_disable_motion_detection(self) -> None:
        """Disable motion detection in camera."""
        if not await self.device.set_recording_mode(RecordingMode.NEVER):
            return
        _LOGGER.debug("Motion Detection Disabled for Camera: %s", self.device.name)

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> None:
        """Return the Camera Image."""
        last_image = await self.device.get_snapshot(width, height)
        self._last_image = last_image
        return self._last_image

    async def stream_source(self) -> None:
        """Return the Stream Source."""
        return self._stream_source
