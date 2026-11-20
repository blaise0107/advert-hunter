import json
import hunter2 as h
kind, a, path, lines = h.read_screen("base3.png", check_focus=False)
print("kind:", kind, "menu_score:", h.menu_score(lines), "active:", h.is_active_list(lines, a))
print(json.dumps([{"t": l["text"], "y": l["y"]} for l in lines], ensure_ascii=False))
