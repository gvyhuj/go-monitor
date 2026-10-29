"""A failed encryption build must never qualify for Pages publication."""
from pathlib import Path
import pytest
from core import sitebuild


def test_failed_build_cannot_be_published(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError('encryption unavailable')
    monkeypatch.setattr(sitebuild, 'encrypt', fail)
    out = tmp_path / 'site'
    with pytest.raises(RuntimeError, match='encryption unavailable'):
        sitebuild.build(out, 'test-password', b'0123456789abcdef', [('live', '{}', False)])
    # The HTML file alone cannot prove that all encrypted payloads were built.
    assert (out / 'index.html').exists()
    assert not (out / 'd' / 'live.bin').exists()
    workflow = Path('.github/workflows/monitor.yml').read_text()
    for action in ('actions/upload-pages-artifact@v3', 'actions/deploy-pages@v4'):
        step = next(s for s in workflow.split('\n      - name:') if 'uses: ' + action in s)
        assert "if: success() && hashFiles('_site/index.html') != ''" in step
    assert "if: always() && hashFiles('state/*.db.enc') != ''" in workflow
