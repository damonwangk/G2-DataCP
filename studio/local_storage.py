"""Web console and recorder run on the same robot. No automatic file transfer."""
from pathlib import Path
from robot_storage import RobotStorage

class LocalStorage(RobotStorage):
    def _monitor(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(.2)

    def start(self, label, stats=None, context=None, topics=None):
        state = self.request('/start', {'label': label, 'topics': topics, 'context': context or {}})
        with self.lock:
            self.cache = dict(state, connected=True)
        self.directory = Path(state['path']).parent
        return state

    def next_episode(self, body):
        result = self.request('/next', body)
        state = result['recording']
        with self.lock:
            self.cache = dict(state, connected=True)
        self.directory = Path(state['path']).parent
        return result

    def stop(self, stats=None):
        result = self.request('/stop', {})
        self.refresh()
        return dict(result, transferring=False)

    def transfer_status(self):
        return []

    def remote_files(self):
        return self.request('/episodes')

    def queue_transfer(self, *args, **kwargs):
        raise ValueError('请在数据导出面板选择当前电脑的保存位置')
