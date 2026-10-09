import ssl

from viper_ide import net


def test_https_context_drops_only_strict_flag():
    ctx = net.https_context()
    assert not ctx.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
