import unittest

from check_backup import check_file


class BackupGuardTests(unittest.TestCase):
    def test_private_and_generated_paths_are_blocked(self):
        for name in ("secrets.yaml", "realtime_relay/.env", "backups/flash.bin", ".esphome/config.json", "voice.wav", ".tmp_headers.txt"):
            with self.subTest(name=name):
                self.assertIsNotNone(check_file(name, b"test"))

    def test_examples_must_still_use_placeholders(self):
        self.assertIsNone(check_file("secrets.example.yaml", b'wifi_password: "YOUR_WIFI_PASSWORD"'))
        self.assertIsNone(check_file("realtime_relay/.env.example", b"SARVAM_API_KEY=YOUR_SARVAM_API_KEY"))
        self.assertIsNotNone(check_file("secrets.example.yaml", b'wifi_password: "not-a-placeholder"'))

    def test_token_patterns_are_blocked_without_reporting_values(self):
        token = b"ghp_" + b"A" * 36
        message = check_file("README.md", token)
        self.assertIsNotNone(message)
        self.assertNotIn(token.decode(), message)

    def test_changed_wake_model_requires_review(self):
        self.assertIsNotNone(check_file("hey_jarvis.tflite", b"different model"))

    def test_runtime_configuration_is_allowed(self):
        self.assertIsNone(check_file("app.py", b'api_key = os.environ["SARVAM_API_KEY"]'))


if __name__ == "__main__":
    unittest.main()
