import sys
import unittest
from unittest.mock import MagicMock, patch, ANY

# --- Mock heavy dependencies before they are imported by the app module ---
# This is crucial for testing a GUI application in a headless environment.
mock_tk = MagicMock()
sys.modules['tkinter'] = mock_tk
sys.modules['tkinter.ttk'] = mock_tk.ttk
sys.modules['tkinter.messagebox'] = mock_tk.messagebox
sys.modules['tkinter.filedialog'] = mock_tk.filedialog
sys.modules['tkinter.simpledialog'] = mock_tk.simpledialog

sys.modules['cv2'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = MagicMock()
sys.modules['PIL.ImageTk'] = MagicMock()
sys.modules['tkinterweb'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['webbrowser'] = MagicMock()

# Now, import the application module
import Camera_testerv2 as app_module


class TestUrlHelpers(unittest.TestCase):
    """Tests for the URL building utility functions."""

    def test_build_basic_auth_url(self):
        """Verify basic auth URL construction for HTTP/HTTPS."""
        self.assertEqual(
            app_module.build_basic_auth_url("http://192.168.1.1/video", "admin", "123"),
            "http://admin:123@192.168.1.1/video"
        )
        self.assertEqual(
            app_module.build_basic_auth_url("https://192.168.1.1:8080", "user", ""),
            "https://user@192.168.1.1:8080"
        )
        self.assertEqual(
            app_module.build_basic_auth_url("http://192.168.1.1", "", ""),
            "http://192.168.1.1"
        )
        self.assertEqual(
            app_module.build_basic_auth_url("http://old:cred@192.168.1.1", "admin", "123"),
            "http://old:cred@192.168.1.1"
        )
        self.assertEqual(
            app_module.build_basic_auth_url("rtsp://192.168.1.1", "admin", "123"),
            "rtsp://192.168.1.1"
        )
        self.assertEqual(
            app_module.build_basic_auth_url("http://192.168.1.1/path?query=1", "a", "b"),
            "http://a:b@192.168.1.1/path?query=1"
        )

    def test_build_rtsp_url(self):
        """Verify credential injection for RTSP URLs."""
        self.assertEqual(
            app_module.build_rtsp_url("rtsp://192.168.1.1/stream1", "admin", "123"),
            "rtsp://admin:123@192.168.1.1/stream1"
        )
        self.assertEqual(
            app_module.build_rtsp_url("rtsp://192.168.1.1:554/live", "user", ""),
            "rtsp://user@192.168.1.1:554/live"
        )
        self.assertEqual(
            app_module.build_rtsp_url("rtsp://192.168.1.1", "", ""),
            "rtsp://192.168.1.1/"
        )
        self.assertEqual(
            app_module.build_rtsp_url("rtsp://old:cred@192.168.1.1", "admin", "123"),
            "rtsp://old:cred@192.168.1.1"
        )
        self.assertEqual(
            app_module.build_rtsp_url("http://192.168.1.1", "admin", "123"),
            "http://192.168.1.1"
        )
        self.assertEqual(
            app_module.build_rtsp_url("rtsp://host/path?q=1#f", "a", "b"),
            "rtsp://a:b@host/path?q=1#f"
        )


@patch('Camera_testerv2.HAS_CV2', True)
@patch('Camera_testerv2.HAS_PIL', True)
@patch('threading.Thread')
@patch('queue.Queue')
class TestRTSPPlayer(unittest.TestCase):
    """Tests for the RTSPPlayer widget."""

    def setUp(self):
        """Set up a fresh RTSPPlayer instance for each test."""
        self.master = MagicMock()
        self.on_status = MagicMock()
        self.player = app_module.RTSPPlayer(self.master, on_status=self.on_status)

    def test_init(self, mock_queue, mock_thread):
        """Verify initial state of the player."""
        self.assertIsNotNone(self.player.canvas)
        self.master.after.assert_called_with(30, self.player._update_canvas)
        mock_queue.assert_called_with(maxsize=2)

    def test_start(self, mock_queue, mock_thread):
        """Verify that start() correctly initiates the stream reading thread."""
        self.player.stop = MagicMock()
        test_url = "rtsp://test.url"
        self.player.start(test_url)

        self.player.stop.assert_called_once()
        self.assertFalse(self.player.stop_event.is_set())
        mock_thread.assert_called_with(target=self.player._reader_loop, args=(test_url,), daemon=True)
        mock_thread.return_value.start.assert_called_once()
        self.on_status.assert_called_with(f"Connecting to {test_url} ...")

    @patch('Camera_testerv2.HAS_CV2', False)
    def test_start_no_cv2_shows_error(self, mock_queue, mock_thread, mock_has_cv2):
        """Verify start() shows an error if OpenCV is missing."""
        self.player.start("rtsp://test.url")
        mock_tk.messagebox.showerror.assert_called_once()
        mock_thread.assert_not_called()

    def test_stop(self, mock_queue, mock_thread):
        """Verify that stop() cleans up the thread and resources."""
        # Simulate a running thread and capture object
        self.player.thread = MagicMock()
        self.player.thread.is_alive.return_value = True
        self.player.cap = MagicMock()

        self.player.stop()

        self.assertTrue(self.player.stop_event.is_set())
        self.player.cap.release.assert_called_once()
        self.assertIsNone(self.player.cap)
        self.player.thread.join.assert_called_with(timeout=2)
        self.on_status.assert_called_with("RTSP stopped.")

    def test_snapshot(self, mock_queue, mock_thread):
        """Verify snapshot functionality."""
        self.player.current_image = "fake_tk_image"
        mock_pil_image = MagicMock()
        self.player._last_pil_image = mock_pil_image

        with patch('os.path.join', return_value='snapshots/snapshot_time.jpg'), \
             patch('datetime.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20230101_120000_000000"

            path = self.player.snapshot(fmt="jpg")

            self.assertEqual(path, 'snapshots/snapshot_time.jpg')
            mock_pil_image.save.assert_called_with('snapshots/snapshot_time.jpg', format="JPEG", quality=92)
            self.on_status.assert_called_with(f"Saved snapshot: {path}")

    def test_snapshot_no_image_shows_warning(self, mock_queue, mock_thread):
        """Verify snapshot shows a warning if no frame is available."""
        self.player.current_image = None
        result = self.player.snapshot()
        self.assertIsNone(result)
        mock_tk.messagebox.showwarning.assert_called_with(app_module.APP_TITLE, "No frame available yet.")


class TestWebPanel(unittest.TestCase):
    """Tests for the WebPanel widget."""

    def setUp(self):
        self.master = MagicMock()
        self.on_status = MagicMock()

    @patch('Camera_testerv2.HAS_TKWEB', True)
    def test_load_with_tkweb(self):
        """Verify URL loading when tkinterweb is available."""
        with patch('tkinterweb.HtmlFrame') as mock_html_frame_class:
            # We need to mock the instance returned by the class
            mock_html_frame_instance = MagicMock()
            mock_html_frame_class.return_value = mock_html_frame_instance

            panel = app_module.WebPanel(self.master, on_status=self.on_status)

            # Test with a full URL
            panel.load("https://example.com")
            mock_html_frame_instance.load_website.assert_called_with("https://example.com")
            self.on_status.assert_called_with("Loading https://example.com ...")

            # Test with a URL needing an http:// prefix
            panel.load("example.com")
            mock_html_frame_instance.load_website.assert_called_with("http://example.com")
            self.on_status.assert_called_with("Loading http://example.com ...")

    @patch('Camera_testerv2.HAS_TKWEB', False)
    def test_load_without_tkweb(self):
        """Verify it shows a warning when tkinterweb is missing."""
        panel = app_module.WebPanel(self.master)
        panel.load("http://example.com")
        mock_tk.messagebox.showwarning.assert_called_once()


@patch('Camera_testerv2.App.on_close') # Prevent self.destroy()
class TestApp(unittest.TestCase):
    """Tests for the main App class logic."""

    def setUp(self):
        """Set up a mocked App instance."""
        # Patch the Tk.__init__ to avoid creating a real window
        with patch('tkinter.Tk.__init__'):
            self.app = app_module.App()

        # Manually create mocks for the UI elements and panels we interact with
        self.app.url_var = MagicMock()
        self.app.user_var = MagicMock()
        self.app.pass_var = MagicMock()
        self.app.notebook = MagicMock()
        self.app.web_panel = MagicMock()
        self.app.rtsp_panel = MagicMock()
        self.app.set_status = MagicMock() # Mock method on instance

        # Set default return values for mocked StringVars
        self.app.url_var.get.return_value = "192.168.1.100"
        self.app.user_var.get.return_value = "testuser"
        self.app.pass_var.get.return_value = "testpass"

    def test_on_load_web(self, mock_on_close):
        """Verify the 'Load (Web)' button action."""
        self.app.on_load_web()

        expected_url = app_module.build_basic_auth_url("192.168.1.100", "testuser", "testpass")

        self.app.notebook.select.assert_called_with(self.app.web_panel)
        self.app.web_panel.load.assert_called_with(expected_url)

    def test_on_open_external(self, mock_on_close):
        """Verify the 'Open in System Browser' action."""
        with patch('webbrowser.open') as mock_webbrowser_open:
            self.app.on_open_external()

            expected_url = app_module.build_basic_auth_url("192.168.1.100", "testuser", "testpass")

            self.app.set_status.assert_called_with(f"Opening in system browser: {expected_url}")
            mock_webbrowser_open.assert_called_with(expected_url)

    def test_on_start_rtsp_with_ip_only(self, mock_on_close):
        """Verify 'Start RTSP' correctly infers URL from an IP address."""
        self.app.url_var.get.return_value = "192.168.1.100"
        self.app.on_start_rtsp()

        inferred_url = "rtsp://192.168.1.100:554/"
        expected_url = app_module.build_rtsp_url(inferred_url, "testuser", "testpass")

        self.app.notebook.select.assert_called_with(self.app.rtsp_panel)
        self.app.rtsp_panel.start.assert_called_with(expected_url)

    def test_on_start_rtsp_with_full_url(self, mock_on_close):
        """Verify 'Start RTSP' uses a full RTSP URL as is."""
        self.app.url_var.get.return_value = "rtsp://mycam.local/live"
        self.app.on_start_rtsp()

        expected_url = app_module.build_rtsp_url("rtsp://mycam.local/live", "testuser", "testpass")

        self.app.notebook.select.assert_called_with(self.app.rtsp_panel)
        self.app.rtsp_panel.start.assert_called_with(expected_url)

    def test_on_start_rtsp_with_http_url_shows_warning(self, mock_on_close):
        """Verify 'Start RTSP' shows a warning for non-RTSP URLs."""
        self.app.url_var.get.return_value = "http://192.168.1.100"
        self.app.on_start_rtsp()

        mock_tk.messagebox.showwarning.assert_called_once()
        self.app.rtsp_panel.start.assert_not_called()

    @patch('tkinter.simpledialog.askstring', return_value='jpg')
    @patch('tkinter.messagebox.askyesno', return_value=False) # Simulate user saying "No"
    def test_on_snapshot(self, mock_askyesno, mock_askstring, mock_on_close):
        """Verify the snapshot action flow."""
        self.app.rtsp_panel.snapshot.return_value = "/path/to/snapshot.jpg"

        self.app.on_snapshot()

        self.app.rtsp_panel.snapshot.assert_called_with(fmt='jpg')
        # Verify it asked the user to copy path and open folder
        self.assertEqual(mock_askyesno.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)