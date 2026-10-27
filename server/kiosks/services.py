from core.models import RaceState
from kiosks.models import Kiosk, SceneKiosk


def apply_scene(state: RaceState, scene_id: int) -> None:
    """`apply_scene()` in `website/inc/scenes.inc`: make the scene current
    and give each kiosk the page the scene sets for its name. A kiosk name
    the scene leaves blank keeps its page. The caller saves `state`."""
    state.current_scene_id = scene_id
    for row in SceneKiosk.objects.filter(scene_id=scene_id).exclude(page=""):
        Kiosk.objects.filter(event_id=state.event_id, name=row.kiosk_name).update(
            page=row.page, params=row.params
        )
