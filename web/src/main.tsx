import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "@tanstack/react-router"
import { Toaster } from "sonner"
import "./index.css"
import { router } from "./app/router"
import { queryClient } from "./lib/query"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "#262626",
            border: "1px solid #3c3c3c",
            borderLeft: "3px solid #ededed",
            borderRadius: "6px",
            color: "#ededed",
            fontFamily: "'IBM Plex Sans', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
            fontSize: "12.5px",
            boxShadow: "0 12px 28px rgba(0,0,0,.5)",
          },
        }}
      />
    </QueryClientProvider>
  </StrictMode>
)
