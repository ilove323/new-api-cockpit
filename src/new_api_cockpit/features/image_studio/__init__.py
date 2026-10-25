"""Independent image feature; imports have no external-service side effects."""


def create_blueprint(host):
    from .http import create_blueprint as factory

    return factory(host)
