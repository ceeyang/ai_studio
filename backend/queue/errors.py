"""任务执行中的控制流异常。"""


class JobCanceled(Exception):
    """任务被取消。处理器检测到取消时抛出。"""


class NeedsInput(Exception):
    """需要人工输入（manual provider）。request 里放给人看的提示词等信息。"""

    def __init__(self, request: dict):
        super().__init__("等待人工输入")
        self.request = request


class JobError(RuntimeError):
    """可预期的业务错误，错误信息原样展示给用户。"""
