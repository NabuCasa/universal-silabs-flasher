import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';

@customElement('usf-icon')
export class UsfIcon extends LitElement {
  @property()
  public icon!: string;

  public render() {
    return html`
      <svg
        preserveAspectRatio="xMidYMid meet"
        focusable="false"
        role="img"
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="currentColor"
      >
        <g>
          <path d=${this.icon}></path>
        </g>
      </svg>
    `;
  }

  static styles = css`
    :host {
      vertical-align: middle;
    }

    svg {
      vertical-align: middle;
      width: 24px;
      height: 24px;

      display: inline-block;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'usf-icon': UsfIcon;
  }
}
