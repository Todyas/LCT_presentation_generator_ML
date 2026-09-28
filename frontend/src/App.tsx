import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ResultPage } from "./pages/ResultPage";
import { StartPage } from "./pages/StartPage";
import { SessionProvider } from "./state";

export function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<StartPage />} />
          <Route path="/result" element={<Navigate to="/" replace />} />
          <Route path="/result/:jobId" element={<ResultPage />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  );
}
