"""P202: _friendly_error must not match 'memory' in unrelated error messages."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.worker import _friendly_error


class TestFriendlyErrorMemoryMatching(unittest.TestCase):

    def test_oom_detected(self):
        """Real OOM errors must still be caught."""
        result = _friendly_error("RuntimeError: CUDA out of memory. Tried to allocate 2 GiB")
        self.assertIn("内存不足", result)

    def test_alloc_memory_detected(self):
        """Allocation failure with 'memory' in proper context."""
        result = _friendly_error("MemoryError: Unable to allocate 4.00 GiB")
        self.assertIn("内存不足", result)

    def test_ytdlp_error_not_memory(self):
        """yt-dlp 'unable to remember' or similar must NOT trigger OOM."""
        result = _friendly_error("ERROR: unable to remember last download position")
        self.assertNotIn("内存不足", result,
                         "'memory' substring in unrelated context must not trigger OOM message")

    def test_generic_memory_word_not_oom(self):
        """A log line mentioning 'memory' casually must not trigger OOM."""
        result = _friendly_error("in memory of the old API, this endpoint is deprecated")
        self.assertNotIn("内存不足", result,
                         "Casual 'memory' mention must not be treated as OOM")

    def test_real_oom_keywords(self):
        """'Cannot allocate memory' from OS must still be caught."""
        result = _friendly_error("OSError: [Errno 12] Cannot allocate memory")
        self.assertIn("内存不足", result)


if __name__ == '__main__':
    unittest.main()
