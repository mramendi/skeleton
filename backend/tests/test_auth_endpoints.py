"""
Tests for authentication endpoints.

Tests login, logout, and token validation.
"""
import pytest


@pytest.mark.auth
@pytest.mark.unit
def test_login_success(client, test_credentials):
    """Test successful login with valid credentials"""
    response = client.post("/login", json={
        "username": test_credentials["username"],
        "password": test_credentials["password"]
    })

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["access_token"] == test_credentials["token"]


@pytest.mark.auth
@pytest.mark.unit
def test_login_invalid_username(client):
    """Test login with invalid username"""
    response = client.post("/login", json={
        "username": "nonexistent",
        "password": "password1"
    })

    assert response.status_code == 401
    assert "Invalid credentials" in response.json()["detail"]


@pytest.mark.auth
@pytest.mark.unit
def test_login_invalid_password(client, test_credentials):
    """Test login with invalid password"""
    response = client.post("/login", json={
        "username": test_credentials["username"],
        "password": "wrongpassword"
    })

    assert response.status_code == 401
    assert "Invalid credentials" in response.json()["detail"]


@pytest.mark.auth
@pytest.mark.unit
def test_login_missing_fields(client):
    """Test login with missing required fields"""
    response = client.post("/login", json={
        "username": "testuser1"
        # Missing password
    })

    assert response.status_code == 422  # Validation error


@pytest.mark.auth
@pytest.mark.unit
def test_logout_authenticated(client, auth_headers):
    """Test logout with valid authentication"""
    response = client.post("/logout", headers=auth_headers)

    assert response.status_code == 200
    assert "Logged out successfully" in response.json()["message"]


@pytest.mark.auth
@pytest.mark.unit
def test_logout_unauthenticated(client):
    """Test logout without authentication"""
    response = client.post("/logout")

    assert response.status_code == 403  # No Authorization header


@pytest.mark.auth
@pytest.mark.unit
def test_logout_invalid_token(client):
    """Test logout with invalid token"""
    response = client.post("/logout", headers={
        "Authorization": "Bearer invalid-token"
    })

    assert response.status_code == 401


@pytest.mark.auth
@pytest.mark.unit
def test_protected_endpoint_requires_auth(client):
    """Test that protected endpoints require authentication"""
    # Try to access protected endpoint without auth
    response = client.get("/api/v1/threads")

    assert response.status_code == 403  # Forbidden


@pytest.mark.auth
@pytest.mark.unit
def test_protected_endpoint_with_auth(client, auth_headers):
    """Test that protected endpoints work with valid auth"""
    response = client.get("/api/v1/threads", headers=auth_headers)

    assert response.status_code == 200
    assert isinstance(response.json(), list)
