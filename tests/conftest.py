def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: integration tests that require live OneDrive credentials and network access",
    )
