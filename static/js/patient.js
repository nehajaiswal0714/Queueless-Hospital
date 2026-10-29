
let patientDoctors = [], patientTests = [];

// Change this if your hospital uses a different appointment duration.
const APPOINTMENT_DURATION_MINUTES = 20;

function displayDateTime(value) {
  if (!value) return 'Time will be updated';

  const d = new Date(value);

  if (Number.isNaN(d.getTime())) {
    return String(value).replace('T', ' ').slice(0, 16);
  }

  return d.toLocaleString([], {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
}

// Display an appointment range such as 9:10 AM – 9:30 AM.
function displayAppointmentRange(value) {
  if (!value) return 'Not scheduled yet';

  const start = new Date(value);

  if (Number.isNaN(start.getTime())) {
    return 'Time will be updated';
  }

  const end = new Date(
    start.getTime() + APPOINTMENT_DURATION_MINUTES * 60000
  );

  const options = {
    hour: 'numeric',
    minute: '2-digit'
  };

  const startTime = start.toLocaleTimeString([], options);
  const endTime = end.toLocaleTimeString([], options);

  return `${startTime} – ${endTime}`;
}

async function loadDepartments() {
  const r = await api('/api/departments');
  const ds = r.departments.filter(d => d.active);

  $('#department').innerHTML =
    '<option value="">Select department</option>' +
    ds.map(d =>
      `<option value="${d.id}">${esc(d.name)} · ${d.waiting_count} waiting</option>`
    ).join('');
}

async function loadDoctors() {
  const dep = $('#department').value;

  const r = await api(
    '/api/doctors' + (dep ? `?department_id=${dep}` : '')
  );

  patientDoctors = r.doctors;

  $('#doctor').innerHTML =
    '<option value="">Select doctor</option>' +
    r.doctors.map(d =>
      `<option value="${d.id}">
        ${esc(d.name)} · ${esc(d.specialization)} · ${money(d.consultation_fee)}
      </option>`
    ).join('');

  $('#doctors').innerHTML = r.doctors.map(d => `
    <div class="doctor-row">
      <div>
        <b>${esc(d.name)}</b>
        <div class="muted">
          ${esc(d.specialization)} · ${esc(d.department_name || 'Unassigned')}
        </div>
        <div class="muted">
          ${time(d.shift_start)} – ${time(d.shift_end)} · ${money(d.consultation_fee)}
        </div>
      </div>

      <div>
        ${statusBadge(d.current_status)}
        ${d.return_minutes
          ? `<div class="muted" style="margin-top:5px">
               Returns in ${d.return_minutes} min
             </div>`
          : ''}
      </div>
    </div>
  `).join('') || empty('No doctors found.');
}

async function bookToken() {
  const department_id = Number($('#department').value);
  const doctor_id = $('#doctor').value
    ? Number($('#doctor').value)
    : null;

  if (!department_id) {
    $('#bookMsg').textContent = 'Please select a department.';
    return;
  }

  try {
    const r = await api('/api/tokens', {
      method: 'POST',
      body: {
        department_id,
        doctor_id
      }
    });

    $('#bookMsg').textContent =
      `Token #${r.token.token_number} booked successfully.`;

    $('#bookMsg').className = 'form-message success-text';

    await loadAll();
  } catch (e) {
    showError(e);
  }
}

// Cancel a patient's waiting token.
async function cancelToken(tokenId) {
  if (!confirm(
    'Are you sure you want to cancel this token? This cannot be undone.'
  )) {
    return;
  }

  try {
    const r = await api(`/api/my-tokens/${tokenId}/cancel`, {
      method: 'PUT',
      body: {}
    });

    toast(r.message || 'Token cancelled successfully');

    await loadAll();
  } catch (e) {
    showError(e);
  }
}

async function loadTokens() {
  const r = await api('/api/my-tokens');

  const active = r.tokens.find(t =>
    ['waiting', 'called'].includes(t.status)
  );

  statCards('#patientStats', [
    {
      label: 'Current token',
      value: active ? `#${active.token_number}` : '—',
      note: active ? active.department_name : 'No active queue'
    },
    {
      label: 'Doctor',
      value: active?.doctor_name || 'Not assigned'
    },
    {
      label: 'Doctor fee',
      value: money(active?.doctor_fee || 0)
    },
    {
      label: 'Balance',
      value: money(
        active
          ? Number(active.total_amount) - Number(active.paid_amount)
          : 0
      )
    }
  ]);

  $('#tokenList').innerHTML = r.tokens.length
    ? r.tokens.map(t => `
        <div class="token-card">

          <div class="mini-card-head">
            <div>
              <small class="muted">${esc(t.department_name)}</small>
              <div class="token-number">#${t.token_number}</div>
            </div>

            ${statusBadge(t.status)}
          </div>

          <div class="token-meta">
            <span>
              Doctor<br>
              <b>${esc(t.doctor_name || 'Unassigned')}</b>
              <div class="appointment-time" style="
                margin-top:6px;
                font-size:12px;
                line-height:1.5;
              ">
                <span class="muted">Your appointment</span><br>
                <b>${esc(displayAppointmentRange(t.scheduled_at))}</b>
              </div>
            </span>

            <span>
              Priority<br>
              <b>${priorityBadge(t.priority)}</b>
            </span>

            <span>
              Fee<br>
              <b>${money(t.doctor_fee)}</b>
            </span>
          </div>

          ${t.queue_note
            ? `<div class="token-note">${esc(t.queue_note)}</div>`
            : ''}

          <div class="muted" style="margin-top:10px;font-size:10px">
            Booked ${esc(t.booked_at || '')}
          </div>

          ${
            t.status === 'waiting'
              ? `<div style="margin-top:12px">
                   <button
                     class="btn small danger"
                     type="button"
                     onclick="cancelToken(${t.id})">
                     Cancel token
                   </button>
                 </div>`
              : ''
          }

        </div>
      `).join('')
    : empty('You have not booked any tokens yet.');
}

async function loadTests() {
  const r = await api('/api/tests');

  patientTests = r.tests;

  $('#testList').innerHTML = r.tests.map(t => `
    <div class="mini-card">
      <div class="mini-card-head">
        <b>${esc(t.name)}</b>
        <b>${money(t.price)}</b>
      </div>
      <p>${esc(t.description || '')}</p>
    </div>
  `).join('') || empty('No tests available.');

  $('#testSelect').innerHTML =
    '<option value="">Select a test</option>' +
    r.tests.map(t =>
      `<option value="${t.id}">
        ${esc(t.name)} · ${money(t.price)}
      </option>`
    ).join('');
}

async function bookSelectedTest() {
  const test_id = Number($('#testSelect').value);

  if (!test_id) {
    toast('Select a test first', 'error');
    return;
  }

  try {
    await api('/api/test-bookings', {
      method: 'POST',
      body: { test_id }
    });

    toast('Test booked successfully');
  } catch (e) {
    showError(e);
  }
}

async function loadSurgeries() {
  const r = await api('/api/surgeries');

  $('#surgeries').innerHTML = r.surgeries.map(s => `
    <div class="mini-card">

      <div class="mini-card-head">
        <b>${esc(s.name)}</b>
        <b>${money(s.base_price)}</b>
      </div>

      <p>${esc(s.description || '')}</p>

      ${(s.schemes || []).map(sc =>
        `<span class="badge success">
          ${esc(sc.name)} · ${sc.discount_percent}% off
        </span>`
      ).join(' ')}

    </div>
  `).join('') || empty('No surgery information available.');
}

async function loadPayments() {
  const r = await api('/api/payments');

  $('#paymentList').innerHTML = r.payments.length
    ? `
      <table class="data-table">
        <thead>
          <tr>
            <th>Token</th>
            <th>Doctor</th>
            <th>Department</th>
            <th>Doctor fee</th>
            <th>Additional</th>
            <th>Total</th>
            <th>Paid</th>
            <th>Balance</th>
            <th>Status</th>
          </tr>
        </thead>

        <tbody>
          ${r.payments.map(p => `
            <tr>
              <td>#${p.token_number}</td>
              <td>${esc(p.doctor_name || 'Unassigned')}</td>
              <td>${esc(p.department_name)}</td>
              <td>${money(p.doctor_fee)}</td>
              <td>${money(p.other_charges)}</td>
              <td><b>${money(p.total_amount)}</b></td>
              <td>${money(p.paid_amount)}</td>
              <td>
                ${money(
                  Number(p.total_amount) - Number(p.paid_amount)
                )}
              </td>
              <td>${statusBadge(p.payment_status)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `
    : empty('No payment records yet.');
}

// Load all patient dashboard sections.
async function loadAll() {
  try {
    await Promise.all([
      loadDepartments(),
      loadTokens(),
      loadTests(),
      loadSurgeries(),
      loadPayments()
    ]);

    await loadDoctors();
  } catch (e) {
    showError(e);
  }
}

// Initialize the patient dashboard.
document.addEventListener('DOMContentLoaded', () => {
  $('#department').addEventListener('change', loadDoctors);

  loadAll();

  // Refresh appointment estimates and token status every 12 seconds.
  setInterval(async () => {
    try {
      await loadTokens();
    } catch (e) {
      console.error('Unable to refresh patient tokens:', e);
    }
  }, 12000);
});
