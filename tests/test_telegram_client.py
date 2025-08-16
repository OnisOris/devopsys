
import unittest
from devopsys.telegram.bot import TelegramClient

class TestTelegramClient(unittest.TestCase):
    def test_methods_exist(self):
        c = TelegramClient(None, None)
        self.assertTrue(hasattr(c, 'send_message'))
        self.assertTrue(hasattr(c, 'send_inline_keyboard'))
        self.assertTrue(hasattr(c, 'edit_message_text'))
        self.assertTrue(hasattr(c, 'set_my_commands'))
        self.assertTrue(hasattr(c, 'send_reply_keyboard'))
