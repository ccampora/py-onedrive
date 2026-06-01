import unittest
from unittest.mock import patch, MagicMock, mock_open
import json
import requests
from Authentication import (
    get_auth_url, 
    open_auth_page, 
    get_bearer_token, 
    get_refresh_token, 
    print_current_bearer,
    get_bearer_auth_header,
    authenticate,
    AUTH_BASE_URL,
    AUTH_CLIENT_ID,
    AUTH_SCOPE,
    AUTH_RESPONSE_TYPE,
    AUTH_REDIRECT_URI,
    CODE_BASE_URL,
    CODE_GRANT_TYPE
)


class TestAuthentication(unittest.TestCase):
    
    def test_get_auth_url(self):
        """Test that get_auth_url returns the correct URL format"""
        expected_url = f'{AUTH_BASE_URL}client_id={AUTH_CLIENT_ID}&scope={AUTH_SCOPE}&response_type={AUTH_RESPONSE_TYPE}&redirect_uri={AUTH_REDIRECT_URI}'
        actual_url = get_auth_url()
        self.assertEqual(actual_url, expected_url)
    
    def test_get_auth_url_contains_required_params(self):
        """Test that the auth URL contains all required parameters"""
        auth_url = get_auth_url()
        self.assertIn('client_id=22c49a0d-d21c-4792-aed1-8f163c982546', auth_url)
        self.assertIn('scope=Files.ReadWrite%20Files.ReadWrite.all%20Sites.ReadWrite.All%20offline_access', auth_url)
        self.assertIn('response_type=code', auth_url)
        self.assertIn('redirect_uri=https://login.microsoftonline.com/common/oauth2/nativeclient', auth_url)
    
    @patch('Authentication.webbrowser.open_new')
    @patch('builtins.input', return_value='https://login.microsoftonline.com/common/oauth2/nativeclient?code=test_code')
    def test_open_auth_page(self, mock_input, mock_webbrowser):
        """Test that open_auth_page opens browser and returns user input"""
        result = open_auth_page()
        
        # Check that webbrowser.open_new was called with the correct URL
        mock_webbrowser.assert_called_once_with(get_auth_url())
        
        # Check that the function returns the user input
        self.assertEqual(result, 'https://login.microsoftonline.com/common/oauth2/nativeclient?code=test_code')
    
    @patch('Authentication.requests.post')
    @patch('Authentication.logger')
    def test_get_bearer_token_success(self, mock_logger, mock_post):
        """Test successful bearer token retrieval"""
        # Mock the response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'access_token': 'test_access_token',
            'token_type': 'Bearer',
            'expires_in': 3600,
            'refresh_token': 'test_refresh_token'
        }
        mock_post.return_value = mock_response
        
        # Call the function
        result = get_bearer_token('test_code')
        
        # Verify the request was made correctly
        mock_post.assert_called_once_with(
            CODE_BASE_URL,
            data={
                "code": 'test_code',
                "client_id": AUTH_CLIENT_ID,
                "redirect_uri": AUTH_REDIRECT_URI,
                "grant_type": CODE_GRANT_TYPE
            }
        )
        
        # Verify the response
        self.assertEqual(result['access_token'], 'test_access_token')
        self.assertEqual(result['refresh_token'], 'test_refresh_token')
    
    @patch('Authentication.get_current_refresh_token', return_value='test_refresh_token')
    @patch('Authentication.requests.post')
    @patch('Authentication.logger')
    def test_get_refresh_token_success(self, mock_logger, mock_post, mock_get_refresh):
        """Test successful refresh token usage"""
        # Mock the response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'access_token': 'new_access_token',
            'token_type': 'Bearer',
            'expires_in': 3600,
            'refresh_token': 'new_refresh_token'
        }
        mock_post.return_value = mock_response
        
        # Call the function
        result = get_refresh_token()
        
        # Verify the request was made correctly
        mock_post.assert_called_once_with(
            CODE_BASE_URL,
            data={
                "client_id": AUTH_CLIENT_ID,
                "redirect_uri": AUTH_REDIRECT_URI,
                "refresh_token": 'test_refresh_token',
                "grant_type": "refresh_token"
            }
        )
        
        # Verify the response
        self.assertEqual(result['access_token'], 'new_access_token')
        self.assertEqual(result['refresh_token'], 'new_refresh_token')
    
    @patch('Authentication.get_current_bearer', return_value='test_bearer_token')
    @patch('Authentication.logger')
    def test_print_current_bearer(self, mock_logger, mock_get_bearer):
        """Test that print_current_bearer logs the correct message"""
        print_current_bearer()
        
        # Verify that logger.debug was called with the correct message
        mock_logger.debug.assert_called_once_with("Current bearer token is: %s", 'test_bearer_token')
    
    @patch('Authentication.get_current_bearer', return_value='test_bearer_token')
    def test_get_bearer_auth_header(self, mock_get_bearer):
        """Test that get_bearer_auth_header returns correct header format"""
        result = get_bearer_auth_header()
        
        expected_header = {"Authorization": "Bearer test_bearer_token"}
        self.assertEqual(result, expected_header)
    
    @patch('Authentication.get_current_bearer', return_value='existing_token')
    @patch('Authentication.get_refresh_token')
    @patch('Authentication.save_bearer_response')
    def test_authenticate_with_existing_token(self, mock_save, mock_refresh, mock_get_bearer):
        """Test authenticate when bearer token already exists"""
        # Mock refresh token response
        mock_refresh.return_value = {'access_token': 'refreshed_token'}
        
        # Call authenticate
        authenticate()
        
        # Verify that refresh token was called and response was saved
        mock_refresh.assert_called_once()
        mock_save.assert_called_once_with({'access_token': 'refreshed_token'})
    
    @patch('Authentication.get_current_bearer', return_value='')
    @patch('Authentication.open_auth_page', return_value='test_code')
    @patch('Authentication.get_bearer_token')
    @patch('Authentication.get_refresh_token')
    @patch('Authentication.save_bearer_response')
    def test_authenticate_without_existing_token(self, mock_save, mock_refresh, mock_get_bearer_token, mock_open_auth, mock_get_bearer):
        """Test authenticate when no bearer token exists"""
        # Mock responses
        mock_get_bearer_token.return_value = {'access_token': 'new_token'}
        mock_refresh.return_value = {'access_token': 'refreshed_token'}
        
        # Call authenticate
        authenticate()
        
        # Verify the flow
        mock_open_auth.assert_called_once()
        mock_get_bearer_token.assert_called_once_with('test_code')
        mock_refresh.assert_called_once()
        
        # Verify save was called twice (once for initial token, once for refresh)
        self.assertEqual(mock_save.call_count, 2)
    
    @patch('Authentication.get_current_bearer', return_value=None)
    @patch('Authentication.open_auth_page', return_value='test_code')
    @patch('Authentication.get_bearer_token')
    @patch('Authentication.get_refresh_token')
    @patch('Authentication.save_bearer_response')
    def test_authenticate_with_none_token(self, mock_save, mock_refresh, mock_get_bearer_token, mock_open_auth, mock_get_bearer):
        """Test authenticate when bearer token is None"""
        # Mock responses
        mock_get_bearer_token.return_value = {'access_token': 'new_token'}
        mock_refresh.return_value = {'access_token': 'refreshed_token'}
        
        # Call authenticate
        authenticate()
        
        # Verify the flow
        mock_open_auth.assert_called_once()
        mock_get_bearer_token.assert_called_once_with('test_code')
        mock_refresh.assert_called_once()
        
        # Verify save was called twice
        self.assertEqual(mock_save.call_count, 2)


class TestAuthenticationConstants(unittest.TestCase):
    """Test that authentication constants are properly defined"""
    
    def test_constants_are_defined(self):
        """Test that all required constants are defined"""
        self.assertIsNotNone(AUTH_BASE_URL)
        self.assertIsNotNone(AUTH_CLIENT_ID)
        self.assertIsNotNone(AUTH_SCOPE)
        self.assertIsNotNone(AUTH_RESPONSE_TYPE)
        self.assertIsNotNone(AUTH_REDIRECT_URI)
        self.assertIsNotNone(CODE_BASE_URL)
        self.assertIsNotNone(CODE_GRANT_TYPE)
    
    def test_constants_values(self):
        """Test that constants have expected values"""
        self.assertEqual(AUTH_CLIENT_ID, "22c49a0d-d21c-4792-aed1-8f163c982546")
        self.assertEqual(AUTH_RESPONSE_TYPE, "code")
        self.assertEqual(CODE_GRANT_TYPE, "authorization_code")
        self.assertIn("microsoft", AUTH_BASE_URL.lower())
        self.assertIn("microsoft", CODE_BASE_URL.lower())


if __name__ == '__main__':
    # Run the tests
    unittest.main()
