import importlib.util
import sys
import types
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
CTFD_ROOT = PLUGIN.parents[1]
DOCKERFILE = CTFD_ROOT / "Dockerfile"
WORKFLOW = CTFD_ROOT.parents[1] / ".github/workflows/build-ctfd.yml"
PINNED_HOST_SHA = "3bf708868d2af1e567686ca3e7e684b537fd0451"


def test_plugin_load_registers_flag_and_assets():
    calls = []
    ctfd = types.ModuleType("CTFd")
    ctfd_plugins = types.ModuleType("CTFd.plugins")
    ctfd_plugins.register_plugin_assets_directory = (
        lambda app, base_path: calls.append((app, base_path))
    )
    fake_flags = types.ModuleType("typed_answer_flags.flags")
    fake_flags.register = lambda app: calls.append((app, "flags"))
    sys.modules.update(
        {
            "CTFd": ctfd,
            "CTFd.plugins": ctfd_plugins,
            "typed_answer_flags.flags": fake_flags,
        }
    )

    spec = importlib.util.spec_from_file_location(
        "typed_answer_flags",
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    plugin = importlib.util.module_from_spec(spec)
    sys.modules["typed_answer_flags"] = plugin
    spec.loader.exec_module(plugin)
    app = object()

    plugin.load(app)

    assert calls == [
        (app, "flags"),
        (app, "/plugins/typed-answer-flags/assets/"),
    ]


def test_admin_templates_store_the_spec_in_data_not_content():
    create = (PLUGIN / "assets/flags/create.html").read_text(encoding="utf-8")
    edit = (PLUGIN / "assets/flags/edit.html").read_text(encoding="utf-8")

    assert 'name="data"' in create
    assert 'name="data"' in edit
    assert 'name="content"' in create
    assert 'name="content"' in edit
    assert 'name="type" value="typed_answer"' in create
    assert 'name="type" value="typed_answer"' in edit
    assert "{{ data }}" in edit


def test_runtime_dependency_is_pinned_to_the_reviewed_ctfgenerator_commit():
    requirements = (PLUGIN / "requirements.txt").read_text(encoding="utf-8")

    assert requirements.strip() == (
        "ctf-generator @ https://github.com/Judgernaut777/CTFGenerator/archive/"
        + PINNED_HOST_SHA
        + ".tar.gz"
    )


def test_ctfd_image_copies_installs_and_owns_the_plugin():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "COPY plugins/typed-answer-flags "
        "/opt/CTFd/CTFd/plugins/typed-answer-flags"
    ) in dockerfile
    assert (
        "pip install --no-cache-dir -r "
        "/opt/CTFd/CTFd/plugins/typed-answer-flags/requirements.txt"
    ) in dockerfile
    assert "/opt/CTFd/CTFd/plugins/typed-answer-flags" in dockerfile.split("chown -R", 1)[1]


def test_ctfd_workflow_installs_and_runs_typed_answer_plugin_tests():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Install typed-answer-flags test dependencies" in workflow
    assert "Run typed-answer-flags unit tests" in workflow
    assert workflow.count("docker/ctfd/plugins/typed-answer-flags") >= 2
