export const Skeleton = ({ w = "100%", h = 14, r = 6, style }) =>
  <span className="skel" style={{ width: w, height: h, borderRadius: r, ...style }} aria-hidden="true" />;

export const SkeletonCard = ({ lines = 3 }) =>
  <div className="card skel-card" aria-busy="true" aria-label="Loading">
    <Skeleton w="40%" h={20} />
    {Array.from({ length: lines }, (_, i) => <Skeleton key={i} w={`${90 - i * 12}%`} />)}
  </div>;

export const SkeletonRows = ({ n = 5, h = 44 }) =>
  <div className="skel-rows" aria-busy="true" aria-label="Loading">
    {Array.from({ length: n }, (_, i) => <Skeleton key={i} h={h} r={8} />)}
  </div>;
