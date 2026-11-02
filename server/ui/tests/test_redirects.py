import pytest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/index.php", "/"),
        ("/login.php", "/login/"),
        ("/login.php?all", "/login/?all"),
        ("/login.php?logout", "/login/?logout"),
        ("/index.php?x=1&y=2", "/?x=1&y=2"),
        ("/derbynet/index.php", "/"),
        ("/derbynet/login.php?all", "/login/?all"),
    ],
)
def test_php_pages_redirect_permanently(client, old, new):
    response = client.get(old)

    assert response.status_code == 301
    assert response["Location"] == new


def test_the_machine_paths_are_not_redirected(client, event):
    assert client.get("/action.php?query=role.list").status_code == 200
