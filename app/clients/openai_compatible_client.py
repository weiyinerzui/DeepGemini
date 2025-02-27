"""OpenAI 兼容格式的客户端类,用于处理符合 OpenAI API 格式的服务"""

import os
import json
from typing import AsyncGenerator, Optional, Union, Dict, Any, List

import aiohttp
from aiohttp.client_exceptions import ClientError

from app.clients.base_client import BaseClient
from app.utils.logger import logger


class OpenAICompatibleClient(BaseClient):
    """OpenAI 兼容格式的客户端类
    
    用于处理符合 OpenAI API 格式的服务,如 Gemini 等
    """

    def __init__(
        self,
        api_key: str,
        api_url: str,
        timeout: Optional[aiohttp.ClientTimeout] = None,
        proxy: Optional[str] = None  # 新增代理参数
    ):
        """初始化 OpenAI 兼容客户端

        Args:
            api_key: API密钥
            api_url: API地址
            timeout: 请求超时设置,None则使用默认值
            proxy: 代理服务器地址
        """
        super().__init__(api_key, api_url, timeout)
        self.proxy = proxy or os.getenv("HTTP_PROXY")  # 优先使用实例化参数

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头

        Returns:
            Dict[str, str]: 请求头字典
        """
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _prepare_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """处理消息格式

        Args:
            messages: 原始消息列表

        Returns:
            List[Dict[str, str]]: 处理后的消息列表
        """
        return messages

    async def chat(
        self, messages: List[Dict[str, str]], model: str
    ) -> Dict[str, Any]:
        """非流式对话

        Args:
            messages: 消息列表
            model: 模型名称

        Returns:
            Dict[str, Any]: OpenAI 格式的完整响应

        Raises:
            ClientError: 请求错误
        """
        headers = self._get_headers()
        processed_messages = self._prepare_messages(messages)

        data = {
            "model": model,
            "messages": processed_messages,
            "stream": False,
        }

        try:
            response_chunks = []
            async for chunk in self._make_request(headers, data):
                response_chunks.append(chunk)
            
            response_text = b"".join(response_chunks).decode("utf-8")
            return json.loads(response_text)

        except Exception as e:
            error_msg = f"Chat请求失败: {str(e)}"
            logger.error(error_msg)
            raise ClientError(error_msg)

    async def stream_chat(
        self, messages: List[Dict[str, str]], model: str
    ) -> AsyncGenerator[tuple[str, str], None]:
        """流式对话

        Args:
            messages: 消息列表
            model: 模型名称

        Yields:
            tuple[str, str]: (role, content) 消息元组
        """
        headers = self._get_headers()
        processed_messages = self._prepare_messages(messages)

        data = {
            "model": model,
            "messages": processed_messages,
            "stream": True,
        }

        buffer = ""  # 用于累积不完整的数据
        try:
            async for chunk in self._make_request(headers, data):
                try:
                    # 解码数据
                    text = chunk.decode('utf-8', errors='ignore')
                    buffer += text

                    # 处理缓冲区中的完整行
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        
                        # 跳过空行
                        if not line:
                            continue
                            
                        # 处理数据行
                        if line.startswith('data: '):
                            json_str = line[6:].strip()
                            
                            # 跳过结束标记
                            if json_str == '[DONE]':
                                continue
                                
                            try:
                                response = json.loads(json_str)
                                if (
                                    "choices" in response
                                    and len(response["choices"]) > 0
                                    and "delta" in response["choices"][0]
                                ):
                                    delta = response["choices"][0]["delta"]
                                    if "content" in delta and delta["content"]:
                                        yield "assistant", delta["content"]
                            except json.JSONDecodeError as e:
                                logger.debug(f"JSON解析错误: {str(e)}, 原始数据: {json_str}")
                                continue

                except UnicodeDecodeError as e:
                    logger.warning(f"解码错误: {str(e)}, 跳过此块数据")
                    continue

        except Exception as e:
            error_msg = f"Stream chat请求失败: {str(e)}"
            logger.error(error_msg)
            raise ClientError(error_msg)

        # 处理缓冲区中剩余的数据
        if buffer:
            try:
                if buffer.startswith('data: '):
                    json_str = buffer[6:].strip()
                    if json_str and json_str != '[DONE]':
                        response = json.loads(json_str)
                        if (
                            "choices" in response
                            and len(response["choices"]) > 0
                            and "delta" in response["choices"][0]
                        ):
                            delta = response["choices"][0]["delta"]
                            if "content" in delta and delta["content"]:
                                yield "assistant", delta["content"]
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.debug(f"处理剩余数据时出错: {str(e)}")

    async def _make_request(
        self, 
        headers: Dict[str, str], 
        data: Dict[str, Any],
        request_timeout: Optional[aiohttp.ClientTimeout] = None
    ) -> AsyncGenerator[bytes, None]:
        """发送请求并处理响应

        Args:
            headers: 请求头
            data: 请求数据
            request_timeout: 请求超时设置

        Yields:
            bytes: 响应数据块
        """
        try:
            connector = aiohttp.TCPConnector(limit=100, force_close=True)
            # 添加代理配置
            proxy = self.proxy if self.proxy and self.proxy.startswith(("http://", "https://")) else None
            logger.debug(f"Using proxy: {proxy}")  # 调试日志

            async with aiohttp.ClientSession(
                connector=connector,
                trust_env=True if not proxy else False  # 禁用自动环境代理检测
            ) as session:
                async with session.post(
                    self.api_url,
                    headers=headers,
                    json=data,
                    timeout=request_timeout,
                    proxy=proxy  # 关键代理注入点
                ) as response:
                    logger.debug(f"Request headers: {headers}")
                    # 修改代理日志记录方式
                    logger.debug(f"Using proxy configuration: {proxy}")
                    
                    # 处理响应
                    if response.status != 200:
                        error_text = await response.text()
                        raise ClientError(f"请求失败，状态码: {response.status}, 错误信息: {error_text}")
                    
                    async for chunk in response.content.iter_chunks():
                        if chunk:
                            yield chunk[0]  # chunk is a tuple of (bytes, bool)

        except aiohttp.ClientError as e:
            error_msg = f"请求发生错误: {str(e)}"
            logger.error(error_msg)
            raise ClientError(error_msg)
        
        except Exception as e:
            error_msg = f"未预期的错误: {str(e)}"
            logger.error(error_msg)
            raise ClientError(error_msg)
