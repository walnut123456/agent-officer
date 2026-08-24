import base64
import io
import os
from http import HTTPStatus
from typing import List

import requests
from PIL import Image

from .embedding import ImageEmbedding
from ..utils import image_utils
from ..utils.logger_utils import logger


def _normalize_dashscope_multimodal_embedding_base_url(base_url: str | None) -> str:
    """兼容把 OpenAI 兼容地址误填为多模态 embedding 地址的场景。"""
    default_url = (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    if not base_url:
        return default_url

    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/compatible-mode/v1"):
        return default_url
    return normalized


class QwenVLEmbedding(ImageEmbedding):
    def __init__(self):
        super().__init__()
        self.timeout = int(os.getenv("API_TIMEOUT", 300))
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.dimension = int(os.getenv("IMAGE_EMBEDDING_DIMENSION", "0"))
        self.model_name = "qwen2.5-vl-embedding"
        self.dashscope_base_url = _normalize_dashscope_multimodal_embedding_base_url(
            os.getenv("DASHSCOPE_MULTIMODAL_EMBEDDING_BASE_URL")
        )

        if os.getenv("DASHSCOPE_MULTIMODAL_EMBEDDING_MODEL_NAME"):
            self.model_name = os.getenv("DASHSCOPE_MULTIMODAL_EMBEDDING_MODEL_NAME")

    @staticmethod
    def _image_to_base64(image: Image.Image) -> str:
        """
        将PIL图像转换为base64编码字符串

        Args:
            image: PIL图像对象

        Returns:
            base64编码的图像字符串
        """
        return "data:image/png;base64," + image_utils.image_to_base64(image)

    def _encode_image(self, image: Image.Image) -> list[float]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        body = {
            "model": self.model_name,
            "input": {
                "contents": [{"image": self._image_to_base64(image)}]
            }
        }
        self._apply_dimension(body)

        resp = requests.post(
            self.dashscope_base_url,
            headers=headers,
            json=body,
            timeout=self.timeout,
            verify=False
        )
        if resp.status_code == HTTPStatus.OK:
            return resp.json()["output"]["embeddings"][0]['embedding']
        else:
            print(resp.text)
            return []

    def _encode_image_batch(self, images: List[Image.Image]) -> list[list[float]]:
        """
        批量编码图片为向量

        Args:
            images: 图片列表

        Returns:
            向量数组，形状为 (len(images), embedding_dim)
        """
        if not images:
            return []

        embeddings = []
        for image in images:
            embedding = self._encode_image(image)
            embeddings.append(embedding)
        return embeddings

    def _encode_text(self, text: str):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            contents = [{"text": text}]
            body = {
                "model": self.model_name,
                "input": {
                    "contents": contents
                }
            }
            self._apply_dimension(body)

            resp = requests.post(
                self.dashscope_base_url,
                headers=headers,
                json=body,
                timeout=self.timeout,
                verify=False
            )
            if resp.status_code == HTTPStatus.OK:

                output_data = resp.json()
                return output_data["output"]["embeddings"][0]["embedding"]

            else:
                logger.error(f"文本编码失败: {resp}")
                return []


        except Exception as e:
            import traceback
            print(traceback.format_exc())
            raise Exception(f"文本编码失败: {e}") from e

    def _encode_text_batch(self, texts: List[str]) -> list[list[float]]:
        """
        批量编码图片为向量

        Args:
            texts: 文本列表

        Returns:
            向量数组，形状为 (len(images), embedding_dim)
        """
        if not texts:
            return []
        embeddings = []
        for text in texts:
            embedding = self._encode_text(text)
            embeddings.append(embedding)
        return embeddings

    def _apply_dimension(self, body: dict) -> None:
        """将配置中的向量维度显式透传给百炼接口，避免依赖服务端默认值。"""
        if self.dimension <= 0:
            return
        body["parameters"] = {"dimension": self.dimension}


class OpenAICompatibleImageEmbedding(ImageEmbedding):
    """
    OpenAI 兼容的多模态 embedding 实现。

    适用于智谱 BigModel 等 OpenAI 兼容网关，通过 /embeddings 接口同时支持文本与图片的向量化。
    """

    def __init__(self):
        super().__init__()
        self.timeout = int(os.getenv("API_TIMEOUT", 300))
        self.api_key = (
            os.getenv("IMAGE_EMBEDDING_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        self.base_url = (os.getenv("IMAGE_EMBEDDING_BASE_URL") or "").rstrip("/")
        self.model_name = os.getenv("IMAGE_EMBEDDING_MODEL_NAME", "embedding-multimodal")
        self.dimension = int(os.getenv("IMAGE_EMBEDDING_DIMENSION", "0"))

    @staticmethod
    def _image_to_base64(image: Image.Image) -> str:
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return "data:image/png;base64," + base64.b64encode(
            buffered.getvalue()
        ).decode("utf-8")

    def _build_body(self, contents: list[dict]) -> dict:
        body = {"model": self.model_name, "input": contents}
        if self.dimension > 0:
            body["dimensions"] = self.dimension
        return body

    def _embed_raw(self, contents: list[dict]) -> list[list[float]]:
        if not contents:
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json=self._build_body(contents),
            timeout=self.timeout,
            verify=False,
        )
        if resp.status_code == HTTPStatus.OK:
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        else:
            logger.error(f"多模态 embedding 请求失败: {resp.status_code} {resp.text[:500]}")
            return [[] for _ in contents]

    def _encode_image(self, image: Image.Image) -> list[float]:
        results = self._embed_raw([{"image": self._image_to_base64(image)}])
        return results[0] if results else []

    def _encode_image_batch(self, images: List[Image.Image]) -> list[list[float]]:
        if not images:
            return []
        contents = [{"image": self._image_to_base64(img)} for img in images]
        return self._embed_raw(contents)

    def _encode_text(self, text: str) -> list[float]:
        results = self._embed_raw([{"text": text}])
        return results[0] if results else []

    def _encode_text_batch(self, texts: List[str]) -> list[list[float]]:
        if not texts:
            return []
        contents = [{"text": t} for t in texts]
        return self._embed_raw(contents)


def get_image_embedding_model() -> ImageEmbedding:
    """获取图像embedding模型"""
    image_embedding_type = (os.getenv("IMAGE_EMBEDDING_TYPE") or "").strip().lower()
    if image_embedding_type == "dashscope":
        return QwenVLEmbedding()
    elif image_embedding_type in {"openai", "openai_compatible", "openai-compatible"}:
        return OpenAICompatibleImageEmbedding()
    else:
        raise ValueError(f"不支持的图像embedding模型: {image_embedding_type}")
