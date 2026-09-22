import { Route, Routes } from "react-router-dom";

import { HomePage } from "./pages/HomePage";
import { OfferPage } from "./pages/OfferPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/offers/:offerId" element={<OfferPage />} />
      <Route path="*" element={<HomePage />} />
    </Routes>
  );
}
