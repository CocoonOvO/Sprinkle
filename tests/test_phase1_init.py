"""Tests for Phase 1: Project Initialization.

This module tests:
- Configuration loading (YAML + env vars)
- Directory structure existence
- Database connection configuration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock

# Import from the sprinkle package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sprinkle.config import (
    AppConfig,
    DatabaseConfig,
    RedisConfig,
    Settings,
    get_settings,
    load_yaml_config,
    AppConfigDC,
    DatabaseConfigDC,
    RedisConfigDC,
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def temp_config_file(tmp_path):
    """Create a temporary config file."""
    config_path = tmp_path / "test_config.yaml"
    config_content = """
app:
  name: "TestApp"
  host: "127.0.0.1"
  port: 9000
  debug: true

database:
  driver: "postgresql"
  host: "db.test.local"
  port: 5433
  name: "test_db"
  user: "test_user"
  password: "test_pass"

redis:
  host: "redis.test.local"
  port: 6380
  db: 1
"""
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def project_root():
    """Get the project root directory."""
    return Path(__file__).parent.parent


# ============================================================================
# Test: Configuration Classes
# ============================================================================

class TestAppConfig:
    """Tests for AppConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = AppConfig()
        assert config.name == "Sprinkle"
        assert config.host == "0.0.0.0"
        assert config.port == 8000
        assert config.debug is False

    def test_custom_values(self):
        """Test custom configuration values."""
        config = AppConfig(
            name="CustomApp",
            host="localhost",
            port=3000,
            debug=True,
        )
        assert config.name == "CustomApp"
        assert config.host == "localhost"
        assert config.port == 3000
        assert config.debug is True


class TestDatabaseConfig:
    """Tests for DatabaseConfig."""

    def test_default_values(self):
        """Test default database configuration."""
        config = DatabaseConfig()
        assert config.driver == "postgresql"
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.name == "sprinkle_db"
        assert config.user == "cream"
        assert config.password == ""

    def test_url_property(self):
        """Test database URL generation."""
        config = DatabaseConfig(
            driver="postgresql",
            host="db.example.com",
            port=5432,
            name="mydb",
            user="admin",
            password="secret",
        )
        expected = "postgresql://admin:secret@db.example.com:5432/mydb"
        assert config.url == expected

    def test_url_with_empty_password(self):
        """Test database URL with empty password."""
        config = DatabaseConfig(
            driver="postgresql",
            host="localhost",
            port=5432,
            name="testdb",
            user="user",
            password="",
        )
        assert "://user:@localhost" in config.url


class TestRedisConfig:
    """Tests for RedisConfig."""

    def test_default_values(self):
        """Test default Redis configuration."""
        config = RedisConfig()
        assert config.host == "localhost"
        assert config.port == 6379
        assert config.db == 0

    def test_url_property(self):
        """Test Redis URL generation."""
        config = RedisConfig(
            host="redis.example.com",
            port=6380,
            db=2,
        )
        expected = "redis://redis.example.com:6380/2"
        assert config.url == expected


# ============================================================================
# Test: YAML Config Loading
# ============================================================================

class TestYamlConfigLoading:
    """Tests for YAML configuration loading."""

    def test_load_yaml_config_from_file(self, temp_config_file):
        """Test loading configuration from YAML file."""
        config = load_yaml_config(str(temp_config_file))
        
        assert "app" in config
        assert config["app"]["name"] == "TestApp"
        assert config["app"]["port"] == 9000
        
        assert "database" in config
        assert config["database"]["host"] == "db.test.local"
        
        assert "redis" in config
        assert config["redis"]["host"] == "redis.test.local"

    def test_load_yaml_config_nonexistent(self, tmp_path):
        """Test loading from non-existent file returns empty dict."""
        config = load_yaml_config(str(tmp_path / "nonexistent.yaml"))
        assert config == {}

    def test_load_yaml_config_with_env_var(self, tmp_path, monkeypatch):
        """Test loading config from path in environment variable."""
        config_path = tmp_path / "env_config.yaml"
        config_path.write_text("""
app:
  name: "EnvConfigApp"
database:
  name: "env_db"
""")
        monkeypatch.setenv("SPRINKLE_CONFIG_PATH", str(config_path))
        
        config = load_yaml_config()
        assert config["app"]["name"] == "EnvConfigApp"


# ============================================================================
# Test: Settings Integration
# ============================================================================

class TestSettings:
    """Tests for the main Settings class."""

    def test_get_settings_default(self):
        """Test getting settings with defaults."""
        settings = get_settings()
        
        assert settings.app.name == "Sprinkle"
        assert settings.database.driver == "postgresql"
        assert settings.redis.host == "localhost"

    def test_get_settings_from_yaml(self, temp_config_file):
        """Test getting settings from YAML file."""
        settings = get_settings(str(temp_config_file))
        
        assert settings.app.name == "TestApp"
        assert settings.app.host == "127.0.0.1"
        assert settings.app.port == 9000
        assert settings.app.debug is True
        
        assert settings.database.host == "db.test.local"
        assert settings.database.port == 5433
        
        assert settings.redis.host == "redis.test.local"
        assert settings.redis.port == 6380

    def test_settings_env_override(self, temp_config_file, monkeypatch):
        """Test environment variable override of YAML config."""
        # Set env vars that should override YAML values
        monkeypatch.setenv("APP__NAME", "EnvOverrideApp")
        monkeypatch.setenv("APP__DEBUG", "true")
        monkeypatch.setenv("DATABASE__HOST", "env-db-host")
        
        settings = get_settings(str(temp_config_file))
        
        # Env vars should take precedence
        assert settings.app.name == "EnvOverrideApp"
        assert settings.app.debug is True
        assert settings.database.host == "env-db-host"


# ============================================================================
# Test: Dataclass Config (Backward Compatibility)
# ============================================================================

class TestDataclassConfig:
    """Tests for dataclass-style configuration."""

    def test_app_config_dc(self):
        """Test AppConfigDC."""
        config = AppConfigDC()
        assert config.name == "Sprinkle"
        assert config.host == "0.0.0.0"
        
        config = AppConfigDC(name="DCApp", port=9000)
        assert config.name == "DCApp"
        assert config.port == 9000

    def test_database_config_dc(self):
        """Test DatabaseConfigDC."""
        config = DatabaseConfigDC()
        assert config.driver == "postgresql"
        
        config = DatabaseConfigDC(host="custom-host", name="custom-db")
        assert config.host == "custom-host"
        assert config.name == "custom-db"

    def test_redis_config_dc(self):
        """Test RedisConfigDC."""
        config = RedisConfigDC()
        assert config.port == 6379
        
        config = RedisConfigDC(host="redis-host", db=5)
        assert config.host == "redis-host"
        assert config.db == 5


# ============================================================================
# Test: Directory Structure
# ============================================================================

class TestDirectoryStructure:
    """Tests for project directory structure."""

    def test_src_sprinkle_exists(self, project_root):
        """Test that src/sprinkle directory exists."""
        src_sprinkle = project_root / "src" / "sprinkle"
        assert src_sprinkle.exists(), "src/sprinkle directory should exist"

    def test_kernel_directory_exists(self, project_root):
        """Test that kernel module exists."""
        kernel_dir = project_root / "src" / "sprinkle" / "kernel"
        assert kernel_dir.exists(), "kernel directory should exist"

    def test_plugins_directory_exists(self, project_root):
        """Test that plugins module exists."""
        plugins_dir = project_root / "src" / "sprinkle" / "plugins"
        assert plugins_dir.exists(), "plugins directory should exist"

    def test_models_directory_exists(self, project_root):
        """Test that models module exists."""
        models_dir = project_root / "src" / "sprinkle" / "models"
        assert models_dir.exists(), "models directory should exist"

    def test_api_directory_exists(self, project_root):
        """Test that api module exists."""
        api_dir = project_root / "src" / "sprinkle" / "api"
        assert api_dir.exists(), "api directory should exist"

    def test_storage_directory_exists(self, project_root):
        """Test that storage module exists."""
        storage_dir = project_root / "src" / "sprinkle" / "storage"
        assert storage_dir.exists(), "storage directory should exist"

    def test_tests_directory_exists(self, project_root):
        """Test that tests directory exists."""
        tests_dir = project_root / "tests"
        assert tests_dir.exists(), "tests directory should exist"

    def test_plugins_dir_exists(self, project_root):
        """Test that plugins directory exists."""
        plugins_dir = project_root / "plugins"
        assert plugins_dir.exists(), "plugins directory should exist"

    def test_data_files_directory_exists(self, project_root):
        """Test that data/files directory exists."""
        data_files_dir = project_root / "data" / "files"
        assert data_files_dir.exists(), "data/files directory should exist"

    def test_init_files_exist(self, project_root):
        """Test that __init__.py files exist in all modules."""
        init_files = [
            project_root / "src" / "sprinkle" / "__init__.py",
            project_root / "src" / "sprinkle" / "kernel" / "__init__.py",
            project_root / "src" / "sprinkle" / "plugins" / "__init__.py",
            project_root / "src" / "sprinkle" / "models" / "__init__.py",
            project_root / "src" / "sprinkle" / "api" / "__init__.py",
            project_root / "src" / "sprinkle" / "storage" / "__init__.py",
        ]
        for init_file in init_files:
            assert init_file.exists(), f"{init_file} should exist"


# ============================================================================
# Test: Main Module and Config Files
# ============================================================================

class TestMainModule:
    """Tests for the main.py module."""

    def test_main_module_imports(self, project_root):
        """Test that main module can be imported."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.main import app
        assert app is not None

    def test_config_module_imports(self, project_root):
        """Test that config module can be imported."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.config import settings
        assert settings.app.name == "Sprinkle"


class TestConfigYamlExample:
    """Tests for config.yaml.example file."""

    def test_config_yaml_example_exists(self, project_root):
        """Test that config.yaml.example exists."""
        config_example = project_root / "config.yaml.example"
        assert config_example.exists(), "config.yaml.example should exist"

    def test_config_yaml_example_valid_yaml(self, project_root):
        """Test that config.yaml.example is valid YAML."""
        import yaml
        config_example = project_root / "config.yaml.example"
        content = config_example.read_text()
        config = yaml.safe_load(content)
        
        assert "app" in config
        assert "database" in config
        assert "redis" in config


# ============================================================================
# Test: Database Connection Configuration
# ============================================================================

class TestDatabaseConnectionConfig:
    """Tests for database connection configuration."""

    def test_database_config_url_format(self):
        """Test database URL format is correct."""
        config = DatabaseConfig(
            driver="postgresql",
            host="localhost",
            port=5432,
            name="testdb",
            user="testuser",
            password="testpass",
        )
        url = config.url
        assert url.startswith("postgresql://")
        assert "testuser:testpass" in url
        assert "localhost:5432" in url
        assert "/testdb" in url

    def test_database_config_default_url(self):
        """Test default database URL generation."""
        config = DatabaseConfig()
        url = config.url
        assert "postgresql://" in url
        assert f":{config.port}" in url

    def test_get_async_engine_function_exists(self, project_root):
        """Test that get_async_engine function exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import get_async_engine
        assert callable(get_async_engine)

    def test_get_async_session_function_exists(self, project_root):
        """Test that get_async_session function exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import get_async_session
        assert callable(get_async_session)

    def test_base_class_exists(self, project_root):
        """Test that SQLAlchemy Base class exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import Base
        assert Base is not None


# ============================================================================
# Test: FastAPI App
# ============================================================================

class TestFastAPIApp:
    """Tests for FastAPI application."""

    def test_app_instance_exists(self, project_root):
        """Test that FastAPI app instance exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.main import app
        assert app is not None

    def test_app_has_root_endpoint(self, project_root):
        """Test that app has root endpoint configured."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.main import app
        
        # Check routes exist
        routes = [r.path for r in app.routes]
        assert "/" in routes or any("root" in str(r) for r in app.routes)

    def test_app_has_health_endpoint(self, project_root):
        """Test that app has health endpoint configured."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.main import app
        
        routes = [r.path for r in app.routes]
        assert "/health" in routes


# ============================================================================
# Test: Database Storage Module
# ============================================================================

class TestDatabaseStorage:
    """Tests for database storage module."""

    def test_base_metadata_exists(self, project_root):
        """Test that Base has metadata for table creation."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import Base
        assert hasattr(Base, "metadata")
        assert Base.metadata is not None

    def test_build_async_db_url(self, project_root):
        """Test the async DB URL builder."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import _build_async_db_url
        from sprinkle.config import DatabaseConfig
        
        config = DatabaseConfig(
            driver="postgresql",
            host="db.example.com",
            port=5432,
            name="mydb",
            user="admin",
            password="secret",
        )
        url = _build_async_db_url(config)
        assert "postgresql+asyncpg://" in url
        assert "admin:secret" in url
        assert "db.example.com:5432" in url

    def test_get_sync_engine_function_exists(self, project_root):
        """Test that get_sync_engine function exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import get_sync_engine
        assert callable(get_sync_engine)

    def test_init_db_function_exists(self, project_root):
        """Test that init_db function exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import init_db
        assert callable(init_db)

    def test_close_db_function_exists(self, project_root):
        """Test that close_db function exists."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage.database import close_db
        assert callable(close_db)


# ============================================================================
# Test: Module Version Constants
# ============================================================================

class TestModuleVersions:
    """Tests for module version constants."""

    def test_sprinkle_version_exists(self, project_root):
        """Test sprinkle package has version."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle import __version__
        assert __version__ is not None
        assert __version__ != ""

    def test_kernel_version_exists(self, project_root):
        """Test kernel module has version."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.kernel import __version__
        assert __version__ is not None

    def test_plugins_version_exists(self, project_root):
        """Test plugins module has version."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.plugins import __version__
        assert __version__ is not None

    def test_models_version_exists(self, project_root):
        """Test models module has version."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.models import __version__
        assert __version__ is not None

    def test_api_version_exists(self, project_root):
        """Test api module has version."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.api import __version__
        assert __version__ is not None

    def test_storage_version_exists(self, project_root):
        """Test storage module has version."""
        sys.path.insert(0, str(project_root / "src"))
        from sprinkle.storage import __version__
        assert __version__ is not None


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=sprinkle", "--cov-report=term-missing"])