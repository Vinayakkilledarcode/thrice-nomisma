import java.util.*;
import java.util.Arrays;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[] arr = new int[n];
        int[] rev = new int[n];
        for(int i=0;i<n;i++){
            arr[i]=sc.nextInt();
        }
        Arrays.sort(arr);
        for(int x:arr){
            System.out.print(x+" ");
        }
        System.out.println("Largest Element: "+arr[n - 1]);
        System.out.println("Second Largest Element: "+arr[n - 2]);
        System.out.println("Smallest Element: "+arr[0]);
        
        for(int i=0;i<n;i++){
            rev[i] = arr[n - i - 1];
        }
        for(int x:rev){
            System.out.print(x+" ");
        }
    }
}