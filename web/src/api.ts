// i64保留HTTP返回的十进制字符串；仅传递和展示，不能转为Number。
export type Int64 = number | string;
export type Account={id:string;username:string;role:'ADMIN'|'USER';enabled:boolean;mustChangePassword:boolean;createdAt:string};
export type Session={account:Account;expiresAt:string};
export class APIError extends Error{constructor(public code:string,message:string){super(message)}}
export async function api<T>(path:string,method='GET',body?:unknown,idempotencyKey=crypto.randomUUID()):Promise<T>{
 const headers:Record<string,string>=body?{'Content-Type':'application/json'}:{};if(method!=='GET')headers['Idempotency-Key']=idempotencyKey;
 const response=await fetch(`/api/v1${path}`,{method,credentials:'same-origin',headers,body:body===undefined?undefined:JSON.stringify(body)});
 const value=await response.json();if(!response.ok)throw new APIError(value.code,value.message);return value as T;
}
