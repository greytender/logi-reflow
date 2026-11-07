"""Constructed packing-search counterexamples; no enterprise performance data."""
import application
import operational_constraints as op


def packing_counterexample(scale=10):
    """One-store A/B/C load-order trap, with fixed orientation and no rehandling.

    Vehicle dimensions are (6,5,1)*scale cm. A=(5,3,1), B=(1,4,1),
    C=(4,1,1), also scaled. Baseline area/id order A,B,C fails. A,C,B
    loads and unloads without changing the route or any policy constraints.
    """
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale <= 0:
        raise ValueError('scale must be a positive number')
    data, _ = op.sample_data()
    order = data['orders'][0]
    data['orders'] = [order]
    keep = {data['settings']['depot_id'], order['id']}
    data['legs'] = {key: value for key, value in data['legs'].items()
                    if set(key.split('>')) <= keep}
    dimensions = {sku: tuple(v*scale for v in dims)
                  for sku, dims in {'A': (5, 3, 1), 'B': (1, 4, 1), 'C': (4, 1, 1)}.items()}
    order['lines'] = [dict(sku=sku, quantity=1, unit_weight_kg=1,
                           unit_volume_m3=x*y*z/1e6)
                      for sku, (x, y, z) in dimensions.items()]
    order.update(pickup=dict(cartons=0, totes=0))
    data = application.canonicalize(data)
    policy = op.sample_policy(data)
    for row in policy['goods']:
        row.update(dict(zip(('length_cm', 'width_cm', 'height_cm'), dimensions[row['sku']])))
        row['max_top_load_kg'] = 0
    for row in policy['vehicle_geometry']:
        row.update(length_cm=6*scale, width_cm=5*scale, height_cm=scale)
    policy['handling']['allow_rehandling'] = False
    return data, op.validate_policy(data, policy)
