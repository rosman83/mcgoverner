import { Component } from "react";

// A blank white page with no explanation is the worst possible failure mode -
// this catches any render-time crash and gives the user a concrete next step
// instead. Data lives server-side (sqlite), so a reload is always safe.
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("Unhandled UI error:", error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="crash-screen">
          <h2>Something went wrong</h2>
          <p className="muted">
            The page hit an unexpected error. Reloading usually fixes it — your data lives on
            the server, so nothing is lost.
          </p>
          <button className="btn" onClick={() => window.location.reload()}>Reload</button>
        </div>
      );
    }
    return this.props.children;
  }
}
