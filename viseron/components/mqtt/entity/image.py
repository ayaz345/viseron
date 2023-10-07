"""MQTT image entity."""
import json

import cv2

from viseron.components.mqtt.const import CONFIG_CLIENT_ID
from viseron.components.mqtt.helpers import PublishPayload
from viseron.helpers.entity.image import ImageEntity

from . import MQTTEntity


class ImageMQTTEntity(MQTTEntity[ImageEntity]):
    """Base image MQTT entity class."""

    @property
    def state_topic(self) -> str:
        """Return state topic."""
        return (
            f"{self._config[CONFIG_CLIENT_ID]}/{self.entity.domain}/"
            f"{self.entity.object_id}/image"
        )

    @property
    def attributes_topic(self) -> str:
        """Return attributes topic."""
        return (
            f"{self._config[CONFIG_CLIENT_ID]}/{self.entity.domain}/"
            f"{self.entity.object_id}/attributes"
        )

    def _create_bytes_image(self):
        """Return numpy image as jpg bytes."""
        if self.entity.image is not None:
            ret, jpg = cv2.imencode(".jpg", self.entity.image)
            if ret:
                return jpg.tobytes()
        return None

    def publish_state(self) -> None:
        """Publish state to MQTT."""
        image = self._create_bytes_image()

        self._mqtt.publish(
            PublishPayload(
                self.state_topic,
                image,
                retain=True,
            )
        )

        payload = {"attributes": self.entity.attributes}
        self._mqtt.publish(
            PublishPayload(
                self.attributes_topic,
                json.dumps(payload),
                retain=True,
            )
        )
