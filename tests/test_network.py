import numpy as np
import pytest

from halibut.network import _from_undirected, watts_strogatz


def _edge_set(net):
    return {(int(a), int(b)) for a, b in zip(net.src, net.dst)}


@pytest.mark.parametrize("p", [0.0, 0.1, 0.5, 1.0])
def test_watts_strogatz_invariants(p):
    n, k = 500, 10
    net = watts_strogatz(n, k, p, np.random.default_rng(0))
    edges = _edge_set(net)
    assert net.num_edges == n * k // 2  # edge count preserved by rewiring
    assert len(edges) == len(net.src)  # no duplicate edges
    assert all(a != b for a, b in edges)  # no self loops
    assert all((b, a) in edges for a, b in edges)  # undirected
    assert net.degree.sum() == n * k
    assert net.degree.mean() == k


def test_ring_lattice_when_no_rewiring():
    net = watts_strogatz(20, 4, 0.0, np.random.default_rng(0))
    assert (net.degree == 4).all()
    assert net.neighbours(0).tolist() == [1, 2, 18, 19]


def test_rewiring_changes_structure_and_is_seeded():
    a = watts_strogatz(500, 10, 0.2, np.random.default_rng(5))
    b = watts_strogatz(500, 10, 0.2, np.random.default_rng(5))
    c = watts_strogatz(500, 10, 0.2, np.random.default_rng(6))
    lattice = watts_strogatz(500, 10, 0.0, np.random.default_rng(5))
    assert _edge_set(a) == _edge_set(b)
    assert _edge_set(a) != _edge_set(c)
    assert a.degree.std() > 0 and lattice.degree.std() == 0


def test_invalid_k():
    with pytest.raises(ValueError):
        watts_strogatz(10, 3, 0.1, np.random.default_rng(0))


def test_neighbour_mean_and_share():
    # path 0-1-2 plus isolated node 3
    net = _from_undirected(4, np.array([0, 1]), np.array([1, 2]))
    assert np.allclose(net.neighbour_mean(np.array([1.0, 2.0, 3.0, 9.0])), [2.0, 2.0, 2.0, 0.0])
    share = net.neighbour_product_share(np.array([1, 0, 2, 2]), 2)
    # node 1's neighbours chose coffee(1) and matcha(2); node 3 is isolated
    assert np.allclose(share, [[0, 0], [0.5, 0.5], [0, 0], [0, 0]])
