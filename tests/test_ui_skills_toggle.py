"""hanno.skills.registry.set_skill_enabled / find_skill_file: the
Skills-tab toggle must flip only the `enabled:` line, preserving every
comment and the rest of a skill file's formatting untouched -- files like
skills/run_code.yaml carry a security-rationale header comment that a full
YAML rewrite would silently drop."""

import textwrap

from hanno.skills.registry import find_skill_file, load_skill_files, set_skill_enabled

RUN_CODE_LIKE = textwrap.dedent("""\
    # THE GENERIC TOOL — DISABLED BY DEFAULT. Read before enabling.
    #
    # This is a security-rationale comment that must survive a toggle.
    name: run_code
    description: >
      Multi-line description
      spanning lines.
    command: ["python3", "scripts/run_code.py"]
    input_mode: stdin
    timeout_seconds: 25
    max_output_chars: 6000
    enabled: false
""")


def _write(dirpath, name, content):
    p = dirpath / name
    p.write_text(content)
    return p


def test_toggle_flips_enabled_and_preserves_comments(tmp_path):
    path = _write(tmp_path, "run_code.yaml", RUN_CODE_LIKE)

    set_skill_enabled(path, True)

    text = path.read_text()
    assert "enabled: true" in text
    assert "# THE GENERIC TOOL — DISABLED BY DEFAULT. Read before enabling." in text
    assert "# This is a security-rationale comment that must survive a toggle." in text
    assert "Multi-line description" in text


def test_toggle_off_then_on_round_trips(tmp_path):
    path = _write(
        tmp_path, "skill.yaml", 'name: x\ndescription: d\ncommand: ["cat"]\nenabled: true\n'
    )

    set_skill_enabled(path, False)
    assert "enabled: false" in path.read_text()
    set_skill_enabled(path, True)
    assert "enabled: true" in path.read_text()


def test_toggle_reflected_by_load_skill_files(tmp_path):
    path = _write(
        tmp_path,
        "skill.yaml",
        'name: togglable\ndescription: d\ncommand: ["cat"]\nenabled: false\n',
    )

    set_skill_enabled(path, True)

    skills = load_skill_files(tmp_path)
    assert skills[0].enabled is True


def test_toggle_missing_enabled_line_raises(tmp_path):
    path = _write(tmp_path, "no_enabled.yaml", 'name: x\ndescription: d\ncommand: ["cat"]\n')
    try:
        set_skill_enabled(path, True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "no top-level 'enabled:' line" in str(e)


def test_find_skill_file_locates_by_name(tmp_path):
    _write(tmp_path, "a.yaml", 'name: alpha\ndescription: d\ncommand: ["cat"]\nenabled: true\n')
    _write(tmp_path, "b.yaml", 'name: beta\ndescription: d\ncommand: ["cat"]\nenabled: true\n')

    found = find_skill_file(tmp_path, "beta")

    assert found is not None
    assert found.name == "b.yaml"


def test_find_skill_file_returns_none_for_unknown_name(tmp_path):
    _write(tmp_path, "a.yaml", 'name: alpha\ndescription: d\ncommand: ["cat"]\nenabled: true\n')
    assert find_skill_file(tmp_path, "nonexistent") is None


def test_find_skill_file_missing_directory_returns_none(tmp_path):
    assert find_skill_file(tmp_path / "does_not_exist", "anything") is None
