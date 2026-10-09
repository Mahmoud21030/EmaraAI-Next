import pytest

from emaraai_next.clock import FakeClock
from emaraai_next.kernel import Kernel
from emaraai_next.store import Store


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def kernel(clock):
    return Kernel(Store(":memory:"), clock, lease_seconds=60)


@pytest.fixture
def project(kernel):
    return kernel.create_project("shop", goal="sell things")["id"]
