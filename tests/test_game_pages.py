from src.api import app


def test_games_endpoint_returns_match_list():
    client = app.test_client()
    response = client.get("/games")

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert any(item.get("status") == "started" for item in data)


def test_game_page_route_exists():
    client = app.test_client()
    response = client.get("/game.html?url=https://example.com/match")

    assert response.status_code == 200
